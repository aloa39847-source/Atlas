"""Read-only multi-timeframe Binance Spot opportunity scanner.

This service discovers liquid USDT Spot symbols, analyzes a bounded shortlist
with the existing technical and decision engines, and never places orders.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from app.core.enums import Timeframe
from app.services.core_service import core_service


_TIMEFRAME_SECONDS = {
    Timeframe.D1: 86400,
    Timeframe.H4: 14400,
    Timeframe.H1: 3600,
    Timeframe.M15: 900,
    Timeframe.M5: 300,
}


class SmartScannerService:
    """Analyze liquid Spot candidates using verified public candles only."""

    def scan(
        self,
        db,
        *,
        max_candidates: int = 10,
        min_quote_volume_usdt: float = 1_000_000.0,
        balance: float = 150.0,
        risk_per_trade: float = 0.005,
        max_data_age_seconds: int = 300,
        environment_mode: str = "demo",
    ) -> dict[str, Any]:
        if environment_mode not in {"demo", "live"}:
            raise ValueError("scanner_environment_mode_must_be_demo_or_live")
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or not 1 <= max_candidates <= 20:
            raise ValueError("max_candidates_must_be_between_1_and_20")
        if any(isinstance(value, bool) for value in (min_quote_volume_usdt, balance, risk_per_trade, max_data_age_seconds)):
            raise ValueError("boolean_scanner_numeric_input_not_allowed")
        try:
            min_quote_volume_usdt = float(min_quote_volume_usdt)
            balance = float(balance)
            risk_per_trade = float(risk_per_trade)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("invalid_scanner_numeric_configuration")
        if (not math.isfinite(min_quote_volume_usdt) or min_quote_volume_usdt < 0):
            raise ValueError("invalid_min_quote_volume_usdt")
        if not math.isfinite(balance) or balance <= 0:
            raise ValueError("invalid_balance")
        if not math.isfinite(risk_per_trade) or not 0 < risk_per_trade <= 0.005:
            raise ValueError("risk_per_trade_must_be_between_0_and_0_005")
        if isinstance(max_data_age_seconds, bool) or not isinstance(max_data_age_seconds, int) or not 30 <= max_data_age_seconds <= 3600:
            raise ValueError("max_data_age_seconds_must_be_between_30_and_3600")

        discovery = core_service.market_data.scan_spot_universe(
            max_symbols=max_candidates,
            min_quote_volume_usdt=float(min_quote_volume_usdt),
        )
        if not discovery.get("ok"):
            raise RuntimeError("spot_universe_discovery_failed")

        scanned_at = datetime.now(timezone.utc)
        results: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for candidate in discovery.get("items", [])[:max_candidates]:
            symbol = str(candidate.get("symbol") or "")
            if not symbol.endswith("/USDT"):
                skipped.append({"symbol": symbol or "unknown", "reason": "unsupported_spot_quote"})
                continue
            try:
                batches = core_service.market_data.get_public_multi_timeframe(symbol)
                coverage: dict[str, dict[str, Any]] = {}
                for timeframe in (Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5):
                    batch = batches.get(timeframe)
                    if batch is None or batch.source.value != "live_public" or len(batch.df) < 30:
                        raise ValueError(f"missing_or_insufficient_public_data:{timeframe.value}")
                    last = batch.df.rows[-1]
                    stamp = last.get("close_time") or last.get("timestamp")
                    if isinstance(stamp, (int, float)):
                        stamp_seconds = float(stamp) / 1000.0
                    elif isinstance(stamp, str):
                        stamp_seconds = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                    else:
                        raise ValueError(f"missing_candle_timestamp:{timeframe.value}")
                    age = scanned_at.timestamp() - stamp_seconds
                    allowed_age = max(float(max_data_age_seconds), _TIMEFRAME_SECONDS[timeframe] * 2.0)
                    if not math.isfinite(age) or age < -60 or age > allowed_age:
                        raise ValueError(f"stale_or_future_candles:{timeframe.value}")
                    coverage[timeframe.value] = {
                        "candles": len(batch.df),
                        "latest_candle": datetime.fromtimestamp(stamp_seconds, timezone.utc).isoformat(),
                        "age_seconds": round(max(age, 0.0), 2),
                        "source": batch.source.value,
                    }

                decision = core_service.analyze(
                    db,
                    symbol=symbol,
                    market_type="spot",
                    environment_mode=environment_mode,
                    balance=float(balance),
                    max_leverage=1.0,
                    risk_per_trade=float(risk_per_trade),
                    data_source="binance_public",
                    market_batches=batches,
                )
                action = str(decision.get("action", "wait")).lower()
                if action not in {"buy", "sell", "wait"}:
                    action = "wait"
                confidence = decision.get("confidence")
                try:
                    confidence = float(confidence) if confidence is not None and not isinstance(confidence, bool) else 0.0
                except (TypeError, ValueError, OverflowError):
                    confidence = 0.0
                if not math.isfinite(confidence):
                    confidence = 0.0
                results.append({
                    "symbol": symbol,
                    "action": action,
                    "confidence": round(max(0.0, min(confidence, 1.0)), 3),
                    "reason": decision.get("reason", "No decision rationale supplied."),
                    "strategy": decision.get("strategy"),
                    "entry_price": decision.get("entry_price"),
                    "stop_loss": decision.get("stop_loss"),
                    "take_profit": decision.get("take_profit"),
                    "risk_reward": decision.get("risk_reward"),
                    "risk": decision.get("risk"),
                    "decision_id": decision.get("decision_id"),
                    "timeframes": decision.get("timeframes", {}),
                    "coverage": coverage,
                    "market_snapshot": candidate,
                    "data_source": "binance_public_read_only",
                    "execution": "not_performed",
                })
            except Exception as exc:
                skipped.append({"symbol": symbol, "reason": str(exc)[:240]})

        # A Spot SELL is position management only; it is never a short entry.
        priority = {"buy": 2, "wait": 1, "sell": 0}
        results.sort(
            key=lambda item: (
                priority.get(item["action"], 0),
                item["confidence"],
                float(item["market_snapshot"].get("quote_volume_usdt_24h") or 0.0),
            ),
            reverse=True,
        )
        for index, item in enumerate(results, start=1):
            item["rank"] = index

        return {
            "ok": True,
            "scanned_at": scanned_at.isoformat(),
            "source": "binance_public_read_only",
            "market": "spot",
            "quote_asset": "USDT",
            "requested_candidates": max_candidates,
            "discovered_candidates": discovery.get("available_candidates", 0),
            "analyzed": len(results),
            "skipped": len(skipped),
            "items": results,
            "skipped_items": skipped,
            "ranking_policy": "Spot BUY candidates first, then confidence, then 24h quote volume. SELL is not a short entry.",
            "execution": "analysis_only_no_shadow_or_exchange_orders",
            "note": "Public market data only. Every listed result includes multi-timeframe coverage. Missing, stale, or invalid data is skipped; confidence is heuristic, not a probability or profit guarantee.",
        }
