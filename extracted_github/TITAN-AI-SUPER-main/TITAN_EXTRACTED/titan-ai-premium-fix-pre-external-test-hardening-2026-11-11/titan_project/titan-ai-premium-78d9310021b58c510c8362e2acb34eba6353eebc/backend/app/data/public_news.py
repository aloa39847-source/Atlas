"""Allowlisted public RSS/Atom crypto-news ingestion; titles and short snippets only."""
from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any


_NEWS_CACHE: dict[tuple[str, ...], tuple[float, dict[str, Any]]] = {}
_NEWS_CACHE_LOCK = threading.Lock()
_NEWS_CACHE_TTL_SECONDS = 180


NEWS_FEEDS = (
    # Primary / official sources: government regulators and protocol foundations.
    {"name": "SEC Press Releases", "url": "https://www.sec.gov/news/pressreleases.rss", "source_type": "official_primary"},
    {"name": "Ethereum Foundation", "url": "https://blog.ethereum.org/feed.xml", "source_type": "official_primary"},
    # Secondary crypto-news publishers retained for context and cross-checking only.
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml", "source_type": "secondary_media"},
    {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss", "source_type": "secondary_media"},
    {"name": "The Block", "url": "https://www.theblock.co/rss.xml", "source_type": "secondary_media"},
    {"name": "Decrypt", "url": "https://decrypt.co/feed", "source_type": "secondary_media"},
)


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _plain_text(value: str, limit: int = 500) -> str:
    parser = _TextOnly()
    try:
        parser.feed(value or "")
        text = " ".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]*>", " ", value or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()[:limit]


def _date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            return None


class _SameHostHttpsRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if (old.scheme != "https" or new.scheme != "https" or not new.hostname
                or new.hostname.lower() != (old.hostname or "").lower()
                or new.port not in (None, 443)
                or new.username is not None or new.password is not None):
            raise urllib.error.HTTPError(req.full_url, code, "cross_host_or_unsafe_news_redirect_blocked", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PublicRSSNewsProvider:
    def __init__(self, feeds=NEWS_FEEDS):
        # Only predefined HTTPS publisher feeds are accepted; callers cannot
        # supply arbitrary URLs or turn this adapter into an SSRF proxy.
        self.feeds = tuple(feeds)

    def _fetch_feed(self, feed: dict[str, str]) -> list[dict[str, Any]]:
        url = feed.get("url", "")
        if not url.startswith("https://") or url not in {item["url"] for item in NEWS_FEEDS}:
            raise ValueError("news_feed_not_allowlisted")
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "TITAN-AI-PREMIUM/1.0", "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"},
        )
        opener = urllib.request.build_opener(_SameHostHttpsRedirect())
        with opener.open(request, timeout=8) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise RuntimeError("news_feed_too_large")
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise RuntimeError("news_feed_dtd_or_entity_declaration_rejected")
        root = ET.fromstring(raw)
        items = []
        # RSS 2.0
        for item in root.findall(".//item"):
            title = _plain_text(item.findtext("title", ""))
            link = (item.findtext("link") or "").strip()
            description = _plain_text(item.findtext("description", ""))
            published = _date(item.findtext("pubDate"))
            if title and link.startswith("https://"):
                items.append({
                    "title": title, "url": link, "summary": description,
                    "published_at": published, "source": feed["name"], "source_type": feed.get("source_type", "secondary_media"),
                })
        # Atom feeds
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//atom:entry", ns):
            title = _plain_text(entry.findtext("atom:title", "", ns))
            link_node = entry.find("atom:link", ns)
            link = (link_node.attrib.get("href", "") if link_node is not None else "").strip()
            summary = _plain_text(entry.findtext("atom:summary", "", ns) or entry.findtext("atom:content", "", ns))
            # updated is not a substitute for original publication time.
            published = _date(entry.findtext("atom:published", "", ns))
            if title and link.startswith("https://"):
                items.append({
                    "title": title, "url": link, "summary": summary,
                    "published_at": published, "source": feed["name"], "source_type": feed.get("source_type", "secondary_media"),
                })
        return items

    def fetch(self, *, limit: int = 50) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            return {"ok": False, "error": "news_limit_out_of_range"}
        cache_key = tuple(feed.get("url", "") for feed in self.feeds)
        now = time.time()
        with _NEWS_CACHE_LOCK:
            cached = _NEWS_CACHE.get(cache_key)
            if cached and now - cached[0] < _NEWS_CACHE_TTL_SECONDS:
                result = dict(cached[1])
                result["items"] = list(cached[1].get("items", []))[:limit]
                result["cache_hit"] = True
                return result

        articles = []
        failures = []
        seen = set()
        # Fetch concurrently so one slow publisher does not serialize the
        # entire analysis request. Each source still has its own short timeout.
        with ThreadPoolExecutor(max_workers=min(6, max(1, len(self.feeds)))) as pool:
            futures = {pool.submit(self._fetch_feed, feed): feed for feed in self.feeds}
            for future in as_completed(futures):
                feed = futures[future]
                try:
                    rows = future.result()
                    for row in rows:
                        key = row["url"]
                        if key not in seen:
                            seen.add(key)
                            articles.append(row)
                except Exception as exc:
                    failures.append({"source": feed.get("name"), "error": str(exc)[:160]})
        articles.sort(key=lambda row: row.get("published_at") or "", reverse=True)
        result = {
            "ok": bool(articles),
            "items": articles[:200],
            "source_count": len(self.feeds),
            "successful_sources": len(self.feeds) - len(failures),
            "failures": failures,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": "allowlisted_public_rss",
            "cache_hit": False,
            "note": "Official primary sources are distinguished from secondary media; headlines and short summaries only. No full articles are copied.",
        }
        with _NEWS_CACHE_LOCK:
            _NEWS_CACHE[cache_key] = (time.time(), result)
        result["items"] = result["items"][:limit]
        return result
