"""Read-only order-book liquidity metrics. No order placement or trade signal."""
from __future__ import annotations

import math
from typing import Any

from app.core.enums import EngineStatus
from app.engines.base import Engine
from app.engines.market_data import MarketDataEngine


class LiquidityAnalysisEngine(Engine):
    key = "liquidity_analysis"
    name = "Liquidity Analysis Engine"

    def __init__(self) -> None:
        self.market_data = MarketDataEngine()

    def status(self):
        return EngineStatus.IMPLEMENTED_PARTIAL

    def analyze_order_book(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(snapshot, dict) or snapshot.get("source") not in {"binance_public_read_only", "binance_futures_public_read_only"}:
            return {"ok": False, "error": "verified_public_order_book_required"}
        names = ("best_bid", "best_ask", "mid_price", "spread_percent",
                 "bid_depth_quote", "ask_depth_quote", "depth_imbalance")
        if any(isinstance(snapshot.get(name), bool) for name in names):
            return {"ok": False, "error": "boolean_order_book_metric_not_allowed"}
        try:
            values = {name: float(snapshot[name]) for name in names}
        except (KeyError, TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "invalid_order_book_metrics"}
        if not all(math.isfinite(value) for value in values.values()):
            return {"ok": False, "error": "non_finite_order_book_metrics"}
        bid_depth = values["bid_depth_quote"]
        ask_depth = values["ask_depth_quote"]
        total_depth = bid_depth + ask_depth
        expected_mid = (values["best_bid"] + values["best_ask"]) / 2.0
        expected_spread = ((values["best_ask"] - values["best_bid"]) / expected_mid) * 100.0
        expected_imbalance = ((bid_depth - ask_depth) / total_depth) if total_depth > 0 else 0.0
        if (values["best_bid"] <= 0 or values["best_ask"] <= values["best_bid"]
                or values["mid_price"] <= 0 or values["spread_percent"] < 0
                or bid_depth < 0 or ask_depth < 0
                or not -1.0 <= values["depth_imbalance"] <= 1.0
                or not all(math.isfinite(value) for value in (total_depth, expected_mid, expected_spread, expected_imbalance))):
            return {"ok": False, "error": "inconsistent_order_book_metrics"}
        if not (
            math.isclose(values["mid_price"], expected_mid, rel_tol=1e-6, abs_tol=1e-8)
            and math.isclose(values["spread_percent"], expected_spread, rel_tol=1e-6, abs_tol=1e-8)
            and math.isclose(values["depth_imbalance"], expected_imbalance, rel_tol=1e-6, abs_tol=1e-8)
        ):
            return {"ok": False, "error": "order_book_metrics_do_not_reconcile"}
        for field, full_depth in (("top5_bid_depth_quote", bid_depth), ("top5_ask_depth_quote", ask_depth)):
            if snapshot.get(field) is not None:
                if isinstance(snapshot[field], bool):
                    return {"ok": False, "error": "invalid_top5_order_book_depth"}
                try:
                    top_depth = float(snapshot[field])
                except (TypeError, ValueError, OverflowError):
                    return {"ok": False, "error": "invalid_top5_order_book_depth"}
                if not math.isfinite(top_depth) or top_depth < 0 or top_depth > full_depth + 1e-8:
                    return {"ok": False, "error": "invalid_top5_order_book_depth"}
        return {
            "ok": True,
            "symbol": snapshot.get("symbol"),
            "source": snapshot.get("source"),
            "spread_percent": round(values["spread_percent"], 6),
            "bid_depth_quote": round(values["bid_depth_quote"], 4),
            "ask_depth_quote": round(values["ask_depth_quote"], 4),
            "depth_imbalance": round(values["depth_imbalance"], 6),
            "top5_bid_depth_quote": snapshot.get("top5_bid_depth_quote"),
            "top5_ask_depth_quote": snapshot.get("top5_ask_depth_quote"),
            "best_bid": values["best_bid"],
            "best_ask": values["best_ask"],
            "mid_price": values["mid_price"],
            "fetched_at": snapshot.get("fetched_at"),
            "signal": "not_generated",
            "execution": "none",
            "note": "Descriptive single-snapshot liquidity metrics only; no spread/depth threshold or trading recommendation is inferred.",
        }

    def fetch_and_analyze(self, symbol: str, limit: int = 100, market_type: str = "spot") -> dict[str, Any]:
        try:
            return self.analyze_order_book(self.market_data.get_public_order_book(symbol, limit, market_type))
        except Exception as exc:
            return {"ok": False, "error": "public_order_book_unavailable", "detail": str(exc)[:200]}
