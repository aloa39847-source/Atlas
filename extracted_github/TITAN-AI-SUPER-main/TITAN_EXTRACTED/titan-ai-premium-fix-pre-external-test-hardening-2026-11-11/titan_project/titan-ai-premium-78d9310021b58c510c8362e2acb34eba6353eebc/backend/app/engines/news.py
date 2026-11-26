"""Transparent keyword-based crypto news tagging; not a trained sentiment model."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.core.enums import EngineStatus
from app.engines.base import Engine
from app.data.public_news import PublicRSSNewsProvider


_POSITIVE = {
    "approval", "approved", "adoption", "partnership", "launch", "upgrade",
    "growth", "surge", "record", "inflow", "bullish", "recovery", "integration",
    "milestone", "funding", "突破",
}
_NEGATIVE = {
    "hack", "exploit", "breach", "lawsuit", "ban", "fraud", "scam",
    "outflow", "liquidation", "bankruptcy", "halt", "delist", "investigation",
    "attack", "vulnerability", "bearish", "collapse",
}
_TOPICS = {
    "regulation": {"regulation", "sec", "cftc", "law", "court", "policy", "ban"},
    "security": {"hack", "exploit", "breach", "attack", "vulnerability", "stolen"},
    "etf": {"etf", "exchange-traded fund", "fund flows"},
    "exchange": {"binance", "coinbase", "exchange", "listing", "delisting"},
    "macro": {"fed", "inflation", "rates", "dollar", "treasury", "jobs"},
    "protocol": {"upgrade", "hard fork", "mainnet", "protocol", "layer 2"},
}
_SYMBOL_TERMS = {
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum", "ether", "eth"),
    "BNB": ("binance coin", "bnb"),
    "SOL": ("solana", "sol"),
    "XRP": ("xrp", "ripple"),
    "ADA": ("cardano", "ada"),
    "DOGE": ("dogecoin", "doge"),
    "AVAX": ("avalanche", "avax"),
    "DOT": ("polkadot", "dot"),
    "LINK": ("chainlink", "link"),
    "LTC": ("litecoin", "ltc"),
    "BCH": ("bitcoin cash", "bch"),
    "TON": ("toncoin", "the open network"),
    "TRX": ("tron", "trx"),
    "UNI": ("uniswap", "uni"),
    "AAVE": ("aave", "aave protocol"),
    "SUI": ("sui", "sui blockchain"),
    "NEAR": ("near protocol", "near"),
    "ARB": ("arbitrum", "arb"),
    "ATOM": ("cosmos", "atom"),
    "XLM": ("stellar", "xlm"),
    "SHIB": ("shiba inu", "shib"),
    "PEPE": ("pepe", "pepe token"),
}


class NewsAnalysisEngine(Engine):
    key = "news_analysis"
    name = "Crypto News Analysis Engine"

    def __init__(self, provider: PublicRSSNewsProvider | None = None):
        self.provider = provider or PublicRSSNewsProvider()

    def status(self):
        return EngineStatus.IMPLEMENTED_PARTIAL

    def analyze_items(self, items: list[dict[str, Any]], *, keyword: str | None = None) -> dict[str, Any]:
        normalized_keyword = (keyword or "").strip().lower()
        keyword_symbol = normalized_keyword.upper() if normalized_keyword.upper() in _SYMBOL_TERMS else None
        analyzed = []
        rejected_stale_count = 0
        for item in items:
            title = str(item.get("title") or "")
            summary = str(item.get("summary") or "")
            raw_text = f"{title} {summary}"
            text = raw_text.lower()
            if keyword_symbol:
                aliases = [alias for alias in _SYMBOL_TERMS[keyword_symbol] if alias != keyword_symbol.lower()]
                keyword_match = (
                    re.search(rf"\b{re.escape(keyword_symbol)}\b", raw_text) is not None
                    or any(alias in text for alias in aliases)
                )
                if not keyword_match:
                    continue
            elif normalized_keyword and normalized_keyword not in text:
                continue
            tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9-]*", text)
            positive_hits = [word for word in tokens if word in _POSITIVE]
            negative_hits = [word for word in tokens if word in _NEGATIVE]
            denominator = max(1, len(positive_hits) + len(negative_hits))
            score = (len(positive_hits) - len(negative_hits)) / denominator
            sentiment = "positive" if score > 0 else ("negative" if score < 0 else "neutral")
            topics = [
                topic for topic, vocabulary in _TOPICS.items()
                if any(
                    re.search(rf"\b{re.escape(term)}\b", text) is not None
                    if re.fullmatch(r"[a-z0-9]+", term) else term in text
                    for term in vocabulary
                )
            ]
            asset_matches = []
            for symbol, aliases in _SYMBOL_TERMS.items():
                full_names = [alias for alias in aliases if alias != symbol.lower()]
                ticker_match = re.search(rf"\b{re.escape(symbol)}\b", raw_text) is not None
                name_match = any(
                    re.search(rf"\b{re.escape(alias)}\b", text) is not None
                    if re.fullmatch(r"[a-z0-9]+", alias) else alias in text
                    for alias in full_names
                )
                if ticker_match or name_match:
                    asset_matches.append(symbol)
            critical_terms = {"hack", "exploit", "breach", "stolen", "drained", "bankruptcy"}
            high_terms = {"lawsuit", "ban", "delist", "investigation", "depeg", "halt"}
            if any(term in tokens for term in critical_terms):
                impact_severity = "critical"
            elif any(term in tokens for term in high_terms) or "regulation" in topics:
                impact_severity = "high"
            elif positive_hits or negative_hits:
                impact_severity = "medium"
            else:
                impact_severity = "low"
            evidence_hits = len(set(positive_hits) | set(negative_hits))
            confidence = min(0.85, 0.25 + evidence_hits * 0.12) if evidence_hits else 0.1
            published_at = item.get("published_at")
            # Fail closed: only a verifiable original publication timestamp
            # within the last 60 minutes may influence news analysis.
            try:
                if not isinstance(published_at, str) or not published_at.strip():
                    raise ValueError("missing_publication_timestamp")
                published_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                if published_dt.tzinfo is None:
                    raise ValueError("publication_timestamp_missing_timezone")
                published_age_seconds = (datetime.now(timezone.utc) - published_dt.astimezone(timezone.utc)).total_seconds()
                if published_age_seconds < 0 or published_age_seconds > 3600:
                    rejected_stale_count += 1
                    continue
                published_age_hours = published_age_seconds / 3600.0
            except (TypeError, ValueError, OverflowError):
                rejected_stale_count += 1
                continue
            freshness = "fresh"
            analyzed.append({
                **item,
                "sentiment": sentiment,
                "sentiment_score": round(score, 4),
                "sentiment_confidence": round(confidence, 4),
                "impact_direction": sentiment,
                "impact_severity": impact_severity,
                "asset_matches": asset_matches,
                "published_age_hours": round(published_age_hours, 2) if published_age_hours is not None else None,
                "freshness": freshness,
                "positive_terms": sorted(set(positive_hits)),
                "negative_terms": sorted(set(negative_hits)),
                "topics": topics,
                "analysis_method": "transparent_keyword_lexicon_with_asset_and_freshness_tags",
            })
        return {
            "ok": True,
            "keyword": keyword,
            "count": len(analyzed),
            "items": analyzed,
            "max_age_minutes": 60,
            "rejected_stale_or_undated_count": rejected_stale_count,
            "signal": "not_generated",
            "execution": "none",
            "note": "Only items with a timezone-aware original publication timestamp aged 0-60 minutes are analyzed; missing, invalid, future, or older items are excluded. Keyword lexicon only; negation, sarcasm, context and source bias are not fully modeled. News sentiment is not a trade signal.",
        }

    def fetch_and_analyze(self, *, keyword: str | None = None, limit: int = 50) -> dict[str, Any]:
        feed = self.provider.fetch(limit=limit)
        if not feed.get("ok"):
            return {"ok": False, "error": "news_sources_unavailable", "feed_status": feed}
        analysis = self.analyze_items(feed.get("items", []), keyword=keyword)
        return {
            **analysis,
            "source": feed.get("source"),
            "source_count": feed.get("source_count"),
            "successful_sources": feed.get("successful_sources"),
            "failures": feed.get("failures", []),
            "fetched_at": feed.get("fetched_at"),
        }
