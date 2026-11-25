"""Recent aggregate-trade taker-flow metrics from public Binance data."""
from __future__ import annotations

import math
from typing import Any

from app.core.enums import EngineStatus
from app.engines.base import Engine
from app.engines.market_data import MarketDataEngine


class OrderFlowEngine(Engine):
    key = "order_flow"
    name = "Order Flow Analysis Engine"

    def __init__(self) -> None:
        self.market_data = MarketDataEngine()

    def status(self):
        return EngineStatus.IMPLEMENTED_PARTIAL

    def analyze_trades(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(snapshot, dict) or snapshot.get("source") not in {"binance_public_read_only", "binance_futures_public_read_only"}:
            return {"ok": False, "error": "verified_public_aggregate_trades_required"}
        fields = ("buy_taker_quote_volume", "sell_taker_quote_volume",
                  "taker_flow_delta_quote", "taker_flow_imbalance", "taker_buy_quote_ratio")
        if any(isinstance(snapshot.get(name), bool) for name in fields):
            return {"ok": False, "error": "boolean_aggregate_trade_metric_not_allowed"}
        try:
            values = {name: float(snapshot[name]) for name in fields}
            raw_count = snapshot["trade_count"]
            if isinstance(raw_count, bool) or not isinstance(raw_count, int):
                raise ValueError("trade_count_must_be_integer")
            count = raw_count
        except (KeyError, TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "invalid_aggregate_trade_metrics"}
        if not all(math.isfinite(value) for value in values.values()):
            return {"ok": False, "error": "non_finite_aggregate_trade_metrics"}
        buy_quote = values["buy_taker_quote_volume"]
        sell_quote = values["sell_taker_quote_volume"]
        total_quote = buy_quote + sell_quote
        expected_delta = buy_quote - sell_quote
        if (count < 1 or buy_quote < 0 or sell_quote < 0 or total_quote <= 0
                or not -1.0 <= values["taker_flow_imbalance"] <= 1.0
                or not 0.0 <= values["taker_buy_quote_ratio"] <= 1.0):
            return {"ok": False, "error": "inconsistent_aggregate_trade_metrics"}
        if not (
            math.isclose(values["taker_flow_delta_quote"], expected_delta, rel_tol=1e-6, abs_tol=1e-6)
            and math.isclose(values["taker_flow_imbalance"], expected_delta / total_quote, rel_tol=1e-6, abs_tol=1e-6)
            and math.isclose(values["taker_buy_quote_ratio"], buy_quote / total_quote, rel_tol=1e-6, abs_tol=1e-6)
        ):
            return {"ok": False, "error": "aggregate_trade_metrics_do_not_reconcile"}
        return {
            "ok": True,
            "symbol": snapshot.get("symbol"),
            "source": snapshot.get("source"),
            "trade_count": count,
            "buy_taker_quote_volume": round(values["buy_taker_quote_volume"], 4),
            "sell_taker_quote_volume": round(values["sell_taker_quote_volume"], 4),
            "taker_flow_delta_quote": round(values["taker_flow_delta_quote"], 4),
            "taker_flow_imbalance": round(values["taker_flow_imbalance"], 6),
            "taker_buy_quote_ratio": round(values["taker_buy_quote_ratio"], 6),
            "first_trade_time": snapshot.get("first_trade_time"),
            "last_trade_time": snapshot.get("last_trade_time"),
            "signal": "not_generated",
            "execution": "none",
            "note": "Recent public aggregate-trade taker flow only; this is not a complete order-flow feed or a trade recommendation.",
        }

    def fetch_and_analyze(self, symbol: str, limit: int = 500, market_type: str = "spot") -> dict[str, Any]:
        try:
            return self.analyze_trades(self.market_data.get_public_order_flow(symbol, limit, market_type))
        except Exception as exc:
            return {"ok": False, "error": "public_order_flow_unavailable", "detail": str(exc)[:200]}
