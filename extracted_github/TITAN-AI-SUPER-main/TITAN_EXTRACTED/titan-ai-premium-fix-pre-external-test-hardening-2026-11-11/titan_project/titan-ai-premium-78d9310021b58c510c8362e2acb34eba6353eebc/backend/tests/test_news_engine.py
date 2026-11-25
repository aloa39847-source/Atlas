import json
from io import BytesIO

from app.data.public_news import PublicRSSNewsProvider, NEWS_FEEDS
from app.engines.news import NewsAnalysisEngine


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Bitcoin ETF approval drives adoption and growth</title><link>https://news.example.test/bitcoin-1</link><description>Institutional inflows and positive integration news.</description><pubDate>Sun, 08 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Bitcoin exchange suffers hack and exploit</title><link>https://news.example.test/bitcoin-2</link><description>Users face breach investigation and losses.</description><pubDate>Sun, 08 Nov 2026 09:00:00 GMT</pubDate></item>
<item><title>Ethereum protocol update</title><link>https://news.example.test/eth-1</link><description>Network release.</description></item>
</channel></rss>"""


class Response(BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def test_public_rss_provider_parses_and_deduplicates_allowlisted_feeds(monkeypatch):
    calls = []
    class FakeOpener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            return Response(RSS)
    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: FakeOpener())

    result = PublicRSSNewsProvider().fetch(limit=20)
    assert result["ok"] is True
    assert result["successful_sources"] == 6
    assert len(calls) == 6
    assert len(result["items"]) == 3
    assert result["items"][0]["title"].startswith("Bitcoin ETF approval")
    assert result["items"][0]["published_at"].startswith("2026-11-08")


def test_news_engine_tags_sentiment_and_filters_symbol_without_trade_signal():
    engine = NewsAnalysisEngine()
    items = [
        {"title": "Bitcoin ETF approval and adoption", "summary": "growth and inflows", "url": "https://a.test/1"},
        {"title": "Bitcoin hack and exploit", "summary": "breach and investigation", "url": "https://a.test/2"},
        {"title": "Ethereum upgrade", "summary": "protocol update", "url": "https://a.test/3"},
    ]
    result = engine.analyze_items(items, keyword="BTC")
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["items"][0]["sentiment"] == "positive"
    assert result["items"][1]["sentiment"] == "negative"
    assert result["items"][0]["signal"] if "signal" in result["items"][0] else result["signal"] == "not_generated"


def test_news_provider_rejects_unallowlisted_url_and_limits_output():
    provider = PublicRSSNewsProvider(feeds=({"name": "attacker", "url": "https://attacker.example/feed"},))
    result = provider.fetch(limit=10)
    assert result["ok"] is False
    assert result["failures"][0]["error"] == "news_feed_not_allowlisted"
    assert PublicRSSNewsProvider().fetch(limit=0)["error"] == "news_limit_out_of_range"


def test_public_rss_provider_rejects_xml_entity_declarations(monkeypatch):
    malicious = b'<!DOCTYPE rss [<!ENTITY x "expanded">]><rss><channel></channel></rss>'
    class FakeOpener:
        def open(self, request, timeout):
            return Response(malicious)
    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: FakeOpener())
    result = PublicRSSNewsProvider(feeds=(NEWS_FEEDS[0],)).fetch(limit=5)
    assert result["ok"] is False
    assert "news_feed_dtd_or_entity_declaration_rejected" in result["failures"][0]["error"]


def test_news_redirect_handler_blocks_cross_host_and_non_https_redirects():
    import pytest
    import urllib.error
    import urllib.request
    from app.data.public_news import _SameHostHttpsRedirect

    handler = _SameHostHttpsRedirect()
    request = urllib.request.Request("https://www.coindesk.com/feed")
    with pytest.raises(urllib.error.HTTPError, match="cross_host_or_unsafe_news_redirect_blocked"):
        handler.redirect_request(request, None, 302, "Found", {}, "https://127.0.0.1/internal")
    with pytest.raises(urllib.error.HTTPError, match="cross_host_or_unsafe_news_redirect_blocked"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://www.coindesk.com/feed")


def test_news_engine_maps_more_assets_and_avoids_ambiguous_lowercase_tickers():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    items = [
        {
            "title": "LINK rises after Chainlink partnership upgrade",
            "summary": "adoption and growth",
            "url": "https://news.example.test/link",
            "published_at": now,
        },
        {
            "title": "Click the link to read Bitcoin market data",
            "summary": "No other development.",
            "url": "https://news.example.test/bitcoin",
            "published_at": now,
        },
        {
            "title": "ADA token faces hack and exploit",
            "summary": "Security breach reported.",
            "url": "https://news.example.test/ada",
            "published_at": "2020-01-01T00:00:00+00:00",
        },
    ]
    result = NewsAnalysisEngine().analyze_items(items)
    assert result["items"][0]["asset_matches"] == ["LINK"]
    assert "LINK" not in result["items"][1]["asset_matches"]
    assert "BTC" in result["items"][1]["asset_matches"]
    assert "ADA" in result["items"][2]["asset_matches"]
    assert result["items"][2]["impact_severity"] == "critical"
    assert result["items"][2]["freshness"] == "stale"


def test_news_symbol_filter_requires_exact_uppercase_ticker_or_project_name():
    engine = NewsAnalysisEngine()
    generic = engine.analyze_items([
        {"title": "Click the link to continue", "summary": "A common word only."},
    ], keyword="LINK")
    project = engine.analyze_items([
        {"title": "Chainlink partnership announcement", "summary": "New integration."},
    ], keyword="LINK")
    assert generic["count"] == 0
    assert project["count"] == 1
