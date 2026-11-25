"""Comparable backtest runs across every candle-based implemented strategy."""
from __future__ import annotations

import math
from typing import Any, Sequence


CANDLE_STRATEGIES = (
    "trend_following_ema",
    "momentum_macd_rsi",
    "mean_reversion_bbands",
    "breakout_structure",
    "scalping_m5",
    "swing_h1_h4",
)
SPECIALIZED_STRATEGIES = ("liquidity_orderflow", "arbitrage")


class StrategyBacktestSuite:
    """Run a consistent candle-based strategy set on one historical dataset."""

    def compare(
        self,
        candles: list[dict],
        *,
        market_type: str,
        timeframe: str,
        strategy_keys: Sequence[str] | None = None,
        initial_cash: float = 150.0,
        fee_rate: float | None = None,
        slippage_rate: float = 0.0005,
        max_notional: float | None = None,
        max_leverage: float = 10.0,
        leverage: float = 1.0,
        funding_events: Sequence[dict] = (),
        pyramiding_enabled: bool = False,
        max_adds: int = 2,
        add_fraction: float = 0.25,
        add_trigger_pct: float = 0.01,
    ) -> dict[str, Any]:
        market = str(market_type).lower()
        if market not in {"spot", "futures"}:
            return {"ok": False, "error": "market_type_must_be_spot_or_futures"}
        selected = list(CANDLE_STRATEGIES if strategy_keys is None else strategy_keys)
        if (not selected or len(selected) > len(CANDLE_STRATEGIES)
                or any(not isinstance(key, str) for key in selected)
                or len(set(selected)) != len(selected)):
            return {"ok": False, "error": "invalid_strategy_key_list"}
        unsupported = [key for key in selected if key not in CANDLE_STRATEGIES]
        if unsupported:
            return {
                "ok": False, "error": "strategy_requires_its_own_historical_data_type",
                "unsupported_strategies": unsupported,
                "specialized_strategies": list(SPECIALIZED_STRATEGIES),
            }
        results = []
        for key in selected:
            tf_value = str(getattr(timeframe, "value", timeframe))
            if key == "scalping_m5" and tf_value != "5m":
                results.append({
                    "strategy_key": key, "ok": False, "status": "skipped_incompatible_timeframe",
                    "error": "scalping_m5_requires_5m_timeframe",
                })
                continue
            if key == "swing_h1_h4" and tf_value not in {"1h", "4h"}:
                results.append({
                    "strategy_key": key, "ok": False, "status": "skipped_incompatible_timeframe",
                    "error": "swing_h1_h4_requires_1h_or_4h_timeframe",
                })
                continue
            if market == "spot":
                from app.engines.backtest import BacktestEngine
                engine = BacktestEngine(
                    fee_rate=0.001 if fee_rate is None else fee_rate,
                    slippage_rate=slippage_rate,
                    initial_cash=initial_cash,
                    max_notional=80.0 if max_notional is None else max_notional,
                )
                result = engine.run_strategy(candles, strategy_key=key, timeframe=timeframe,
                                            pyramiding_enabled=pyramiding_enabled, max_adds=max_adds,
                                            add_fraction=add_fraction, add_trigger_pct=add_trigger_pct)
            else:
                from app.engines.futures_backtest import FuturesBacktestEngine
                engine = FuturesBacktestEngine(
                    initial_cash=initial_cash,
                    fee_rate=0.0004 if fee_rate is None else fee_rate,
                    slippage_rate=slippage_rate,
                    max_notional=800.0 if max_notional is None else max_notional,
                    max_leverage=max_leverage,
                )
                result = engine.run_strategy(
                    candles, strategy_key=key, timeframe=timeframe,
                    funding_events=funding_events, leverage=leverage,
                    pyramiding_enabled=pyramiding_enabled, max_adds=max_adds,
                    add_fraction=add_fraction, add_trigger_pct=add_trigger_pct,
                )
            results.append({"strategy_key": key, **result})

        successful = [item for item in results if item.get("ok") is True]

        def metric(item: dict[str, Any], key: str, default: float = 0.0) -> float:
            value = item.get(key, default)
            if isinstance(value, bool):
                return default
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                return default
            return number if math.isfinite(number) else default

        def profit_factor(item: dict[str, Any]) -> float:
            value = item.get("profit_factor")
            if value == "infinite":
                return math.inf
            return metric(item, "profit_factor", 0.0)

        # Return remains the primary objective; drawdown is the first tie-break,
        # followed by profit factor, expectancy and sample size. This avoids
        # presenting a high-return/high-drawdown run as unambiguously superior.
        ranked = sorted(
            successful,
            key=lambda item: (
                metric(item, "total_return"),
                -max(0.0, metric(item, "max_drawdown")),
                profit_factor(item),
                metric(item, "expectancy"),
                int(max(0.0, metric(item, "total_trades"))),
            ),
            reverse=True,
        )
        for rank, item in enumerate(ranked, start=1):
            item["rank"] = rank
        return {
            "ok": bool(successful),
            "market_type": market,
            "timeframe": str(timeframe),
            "strategies_requested": selected,
            "strategies_tested": len(results),
            "strategies_succeeded": len(successful),
            "leaderboard": ranked,
            "results": results,
            "ranking_metric": "total_return_then_lower_drawdown_then_profit_factor_then_expectancy",
            "ranking_fields": ["total_return", "max_drawdown", "profit_factor", "expectancy", "total_trades"],
            "specialized_strategies_not_run_on_candles": list(SPECIALIZED_STRATEGIES),
            "specialized_data_note": (
                "Order Flow requires timestamped order-book and taker-flow history; "
                "triangular arbitrage requires synchronized three-market quote snapshots. "
                "Use the specialized replay endpoint for those datasets."
            ),
            "execution": "simulated_only",
            "no_live_orders": True,
            "limitations": [
                "each strategy is tested independently with a fresh starting balance",
                "ranking is historical only and is not a future-profitability prediction",
                "Spot and Futures use their own execution and fee models",
                "each sleeve maintains one net position; optional pyramiding adds to that position within its notional cap",
                "pyramiding is disabled by default and only applies to historical simulations",
            ],
        }
