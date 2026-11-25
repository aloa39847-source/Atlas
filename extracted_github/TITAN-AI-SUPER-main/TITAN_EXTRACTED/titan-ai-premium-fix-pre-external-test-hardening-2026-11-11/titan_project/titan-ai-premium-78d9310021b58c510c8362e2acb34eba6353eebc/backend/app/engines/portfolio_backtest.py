"""Multi-symbol, multi-strategy portfolio backtesting with isolated capital sleeves."""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from app.engines.dynamic_allocation import propose_dynamic_weights


CANDLE_STRATEGIES = (
    "trend_following_ema",
    "momentum_macd_rsi",
    "mean_reversion_bbands",
    "breakout_structure",
    "scalping_m5",
    "swing_h1_h4",
)


class PortfolioBacktestEngine:
    def run(
        self,
        histories: Mapping[str, list[dict]],
        *,
        market_type: str,
        timeframe: str,
        strategy_key: str | None = None,
        strategy_keys: Sequence[str] | None = None,
        initial_cash: float = 150.0,
        max_positions: int = 10,
        max_notional_per_position: float = 80.0,
        fee_rate: float | None = None,
        slippage_rate: float = 0.0005,
        leverage: float = 1.0,
        max_leverage: float = 10.0,
        funding_by_symbol: Mapping[str, Sequence[dict]] | None = None,
        pyramiding_enabled: bool = False,
        max_adds: int = 2,
        add_fraction: float = 0.25,
        add_trigger_pct: float = 0.01,
        strategy_allocation_weights: Mapping[str, float] | None = None,
        strategy_outcomes: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        allocation_regime: str = "unknown",
    ) -> dict[str, Any]:
        market = str(market_type).lower()
        if (not isinstance(pyramiding_enabled, bool) or isinstance(max_adds, bool)
                or not isinstance(max_adds, int) or not 1 <= max_adds <= 5
                or any(isinstance(value, bool) for value in (add_fraction, add_trigger_pct))):
            return {"ok": False, "error": "invalid_pyramiding_configuration"}
        try:
            add_fraction, add_trigger_pct = float(add_fraction), float(add_trigger_pct)
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "invalid_pyramiding_configuration"}
        if not math.isfinite(add_fraction) or not math.isfinite(add_trigger_pct) or not 0 < add_fraction <= 1 or not 0 < add_trigger_pct <= 0.1:
            return {"ok": False, "error": "pyramiding_configuration_out_of_range"}
        if market not in {"spot", "futures"}:
            return {"ok": False, "error": "market_type_must_be_spot_or_futures"}
        if not isinstance(histories, Mapping) or not 2 <= len(histories) <= 10:
            return {"ok": False, "error": "portfolio_requires_two_to_ten_symbols"}
        if isinstance(max_positions, bool) or not isinstance(max_positions, int) or not 2 <= max_positions <= 60:
            return {"ok": False, "error": "max_positions_must_be_between_2_and_60"}
        numeric = (initial_cash, max_notional_per_position, slippage_rate, leverage, max_leverage)
        if any(isinstance(value, bool) for value in numeric):
            return {"ok": False, "error": "boolean_portfolio_configuration_not_allowed"}
        try:
            cash_value, position_cap, slip, leverage_value, leverage_cap = map(float, numeric)
            fee_value = None if fee_rate is None else float(fee_rate)
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "invalid_portfolio_configuration"}
        if (not all(math.isfinite(value) for value in (cash_value, position_cap, slip, leverage_value, leverage_cap))
                or (fee_value is not None and not math.isfinite(fee_value))
                or cash_value <= 0 or position_cap <= 0 or not 0 <= slip < 1
                or leverage_value < 1 or leverage_cap < 1 or leverage_value > leverage_cap
                or (fee_value is not None and not 0 <= fee_value < 1)):
            return {"ok": False, "error": "portfolio_configuration_out_of_range"}

        if strategy_keys is None:
            selected_strategies = [strategy_key] if strategy_key else []
        else:
            if isinstance(strategy_keys, (str, bytes)):
                return {"ok": False, "error": "strategy_keys_must_be_a_sequence"}
            selected_strategies = list(strategy_keys)
        if (not selected_strategies or len(selected_strategies) > len(CANDLE_STRATEGIES)
                or any(not isinstance(key, str) or key not in CANDLE_STRATEGIES for key in selected_strategies)
                or len(set(selected_strategies)) != len(selected_strategies)):
            return {"ok": False, "error": "invalid_or_duplicate_portfolio_strategy_keys"}
        if strategy_allocation_weights is None:
            raw_weights = {key: 1.0 for key in selected_strategies}
        else:
            if not isinstance(strategy_allocation_weights, Mapping):
                return {"ok": False, "error": "strategy_allocation_weights_must_be_a_mapping"}
            if any(key not in selected_strategies for key in strategy_allocation_weights):
                return {"ok": False, "error": "allocation_weight_for_unselected_strategy"}
            raw_weights = {}
            for key in selected_strategies:
                value = strategy_allocation_weights.get(key, 1.0)
                if isinstance(value, bool):
                    return {"ok": False, "error": "boolean_strategy_allocation_weight"}
                try:
                    weight = float(value)
                except (TypeError, ValueError, OverflowError):
                    return {"ok": False, "error": "invalid_strategy_allocation_weight"}
                if not math.isfinite(weight) or weight <= 0:
                    return {"ok": False, "error": "strategy_allocation_weight_must_be_positive_finite"}
                raw_weights[key] = weight
        weight_total = sum(raw_weights.values())
        normalized_weights = {key: value / weight_total for key, value in raw_weights.items()}
        dynamic_allocation = None
        if strategy_allocation_weights is None and strategy_outcomes is not None:
            if not isinstance(strategy_outcomes, Mapping) or any(key not in selected_strategies for key in strategy_outcomes):
                return {"ok": False, "error": "invalid_dynamic_strategy_outcomes"}
            dynamic_allocation = propose_dynamic_weights(
                {key: strategy_outcomes.get(key, ()) for key in selected_strategies},
                regime=allocation_regime,
            )
            if not dynamic_allocation.get("ok"):
                return {"ok": False, "error": "dynamic_allocation_rejected",
                        "details": dynamic_allocation}
            normalized_weights = dynamic_allocation["weights"]

        sleeve_count = len(histories) * len(selected_strategies)
        if sleeve_count > max_positions:
            return {
                "ok": False, "error": "strategy_symbol_sleeves_exceed_max_concurrent_positions",
                "required_position_slots": sleeve_count, "max_positions": max_positions,
            }

        normalized: dict[str, list[dict]] = {}
        timestamp_sets = []
        for raw_symbol, candles in histories.items():
            symbol = str(raw_symbol).strip().upper().replace("/", "")
            if not symbol.isascii() or not symbol.isalnum() or not 5 <= len(symbol) <= 30:
                return {"ok": False, "error": "invalid_portfolio_symbol"}
            if symbol in normalized or not isinstance(candles, list) or len(candles) < 60:
                return {"ok": False, "error": "duplicate_symbol_or_insufficient_history"}
            stamps = []
            for candle in candles:
                if not isinstance(candle, dict):
                    return {"ok": False, "error": f"invalid_candle_for_{symbol}"}
                stamp = candle.get("timestamp", candle.get("close_time"))
                if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
                    return {"ok": False, "error": f"timestamp_required_for_{symbol}"}
                if not math.isfinite(float(stamp)) or not float(stamp).is_integer():
                    return {"ok": False, "error": f"invalid_timestamp_for_{symbol}"}
                stamps.append(int(stamp))
            if any(stamps[i] <= stamps[i - 1] for i in range(1, len(stamps))):
                return {"ok": False, "error": f"timestamps_not_increasing_for_{symbol}"}
            normalized[symbol] = candles
            timestamp_sets.append(stamps)

        common = set(timestamp_sets[0])
        for stamps in timestamp_sets[1:]:
            common.intersection_update(stamps)
        aligned = sorted(common)
        if len(aligned) < 60:
            return {"ok": False, "error": "portfolio_histories_need_60_common_timestamps"}
        for symbol, candles in list(normalized.items()):
            by_stamp = {
                int(candle.get("timestamp", candle.get("close_time"))): candle
                for candle in candles
            }
            normalized[symbol] = [by_stamp[stamp] for stamp in aligned]

        from app.engines.strategy_backtest_suite import StrategyBacktestSuite
        capital_per_sleeve = cash_value / sleeve_count
        suite = StrategyBacktestSuite()
        per_symbol: dict[str, dict[str, Any]] = {}
        sleeve_curves = []
        all_trades = []
        total_fees = total_slippage = total_funding = 0.0
        liquidations = 0
        successful_sleeves = 0
        successful_symbols = set()
        successful_strategies = set()
        for symbol, candles in normalized.items():
            per_symbol[symbol] = {}
            funding = (funding_by_symbol or {}).get(symbol, ())
            for selected_key in selected_strategies:
                sleeve_capital = cash_value * normalized_weights[selected_key] / len(normalized)
                if market == "spot":
                    per_sleeve_cap = min(position_cap, 80.0, sleeve_capital)
                else:
                    per_sleeve_cap = min(position_cap, 800.0, sleeve_capital * leverage_value)
                comparison = suite.compare(
                    candles, market_type=market, timeframe=timeframe,
                    strategy_keys=[selected_key], initial_cash=sleeve_capital,
                    fee_rate=fee_value, slippage_rate=slip,
                    max_notional=per_sleeve_cap,
                    max_leverage=leverage_cap, leverage=leverage_value,
                    funding_events=funding,
                    pyramiding_enabled=pyramiding_enabled, max_adds=max_adds,
                    add_fraction=add_fraction, add_trigger_pct=add_trigger_pct,
                )
                row = comparison.get("leaderboard", [{}])[0] if comparison.get("leaderboard") else {}
                if row.get("ok") is not True:
                    per_symbol[symbol][selected_key] = {
                        "ok": False,
                        "error": row.get("error", comparison.get("error", "strategy_backtest_failed")),
                        "capital_allocated": round(sleeve_capital, 8),
                    }
                    sleeve_curves.append([sleeve_capital] * len(aligned))
                    continue
                curve = row.get("equity_curve")
                if not isinstance(curve, list) or len(curve) != len(aligned):
                    return {"ok": False, "error": f"equity_curve_length_mismatch_for_{symbol}_{selected_key}"}
                trades = [
                    dict(trade, symbol=symbol, strategy_key=selected_key)
                    for trade in row.get("trades", [])
                ]
                per_symbol[symbol][selected_key] = {
                    "ok": True, "capital_allocated": round(sleeve_capital, 8),
                    "final_equity": row.get("final_equity"),
                    "total_return": row.get("total_return"),
                    "total_trades": row.get("total_trades", len(trades)),
                    "trades": trades,
                    "max_notional_per_position": round(per_sleeve_cap, 8),
                    "max_concurrent_positions": 1,
                }
                sleeve_curves.append([float(value) for value in curve])
                all_trades.extend(trades)
                total_fees += float(row.get("fees_paid", 0.0) or 0.0)
                total_slippage += float(row.get("slippage_cost", 0.0) or 0.0)
                total_funding += float(row.get("funding_net", 0.0) or 0.0)
                liquidations += int(row.get("liquidations", 0) or 0)
                successful_sleeves += 1
                successful_symbols.add(symbol)
                successful_strategies.add(selected_key)

        portfolio_curve = [
            round(sum(curve[index] for curve in sleeve_curves), 8)
            for index in range(len(aligned))
        ]
        final_equity = portfolio_curve[-1]
        peak = portfolio_curve[0]
        max_drawdown = 0.0
        for value in portfolio_curve:
            peak = max(peak, value)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - value) / peak)
        pnl = [float(trade.get("net_pnl", 0.0) or 0.0) for trade in all_trades]
        wins = [value for value in pnl if value > 0]
        losses = [value for value in pnl if value < 0]
        max_concurrent = 0
        for index in range(len(aligned)):
            active = sum(
                1 for trade in all_trades
                if int(trade.get("entry_index", 10**12)) <= index
                < int(trade.get("exit_index", -1))
            )
            max_concurrent = max(max_concurrent, active)
        result = {
            "ok": successful_sleeves > 0,
            "market_type": market,
            "strategy_key": selected_strategies[0] if len(selected_strategies) == 1 else None,
            "strategy_keys": selected_strategies,
            "timeframe": str(timeframe), "symbols_requested": list(normalized),
            "symbols_tested": len(normalized), "symbols_succeeded": len(successful_symbols),
            "strategies_succeeded": len(successful_strategies),
            "sleeves_requested": sleeve_count, "sleeves_succeeded": successful_sleeves,
            "initial_cash": round(cash_value, 8),
            "capital_per_sleeve": round(capital_per_sleeve, 8),
            "strategy_allocation_weights": {key: round(value, 8) for key, value in normalized_weights.items()},
            "dynamic_allocation": dynamic_allocation,
            "capital_allocated_by_strategy": {
                key: round(cash_value * weight, 8) for key, weight in normalized_weights.items()
            },
            "capital_allocation_method": ("dynamic_research_proposal" if dynamic_allocation is not None else "strategy_weighted_symbol_equal" if strategy_allocation_weights is not None else "equal"),
            "capital_per_symbol": round(capital_per_sleeve * len(selected_strategies), 8)
                if len(selected_strategies) == 1 else None,
            "max_positions": max_positions,
            "max_concurrent_positions_observed": max_concurrent,
            "max_notional_per_position": round(per_sleeve_cap, 8),
            "total_trades": len(all_trades),
            "win_rate": round(len(wins) / len(pnl), 4) if pnl else 0.0,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 6) if losses else (None if not wins else "infinite"),
            "total_return": round((final_equity - cash_value) / cash_value, 8),
            "final_equity": final_equity,
            "max_drawdown": round(max_drawdown, 8),
            "fees_paid": round(total_fees, 8),
            "slippage_cost": round(total_slippage, 8),
            "funding_net": round(total_funding, 8),
            "liquidations": liquidations,
            "trades": all_trades,
            "equity_curve": portfolio_curve,
            "timestamps": aligned,
            "per_symbol": per_symbol,
            "capital_model": "equal_weight_isolated_symbol_strategy_sleeves",
            "position_model": "one_net_position_per_symbol_strategy_sleeve_with_optional_bounded_pyramiding",
            "pyramiding_enabled": pyramiding_enabled,
            "max_adds_per_sleeve": max_adds if pyramiding_enabled else 0,
            "add_fraction_of_initial_entry": add_fraction if pyramiding_enabled else 0.0,
            "add_trigger_pct": add_trigger_pct if pyramiding_enabled else 0.0,
            "no_lookahead": True,
            "execution": "simulated_only", "no_live_orders": True,
            "limitations": [
                "capital is divided equally among each symbol-strategy sleeve; sleeves do not dynamically rebalance",
                "multiple strategies may hold the same symbol concurrently; each sleeve maintains one net position",
                "optional pyramiding adds to the existing sleeve position in the favorable direction, subject to the sleeve notional cap",
                "Spot remains long-only; Futures uses the dedicated isolated-margin and funding simulator",
                "cross-margin netting and exchange-native protective orders are not modeled",
                "results depend on each strategy's own causal signals and historical data quality",
            ],
        }
        return result
