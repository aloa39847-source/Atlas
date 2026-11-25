"""Historical, signal-driven Spot backtesting.

Signals are evaluated at their candle index and orders are filled at the NEXT
candle open with configured slippage. No synthetic fixed-return exits are used.
This is a basic single-position simulator, not a claim of walk-forward validation.
"""
from __future__ import annotations

from typing import Any
from dataclasses import dataclass
import math


STRATEGY_PARAMETER_DEFAULTS: dict[str, dict[str, Any]] = {
    "trend_following_ema": {"min_confirmations": 2},
    "momentum_macd_rsi": {"rsi_buy_min": 50.0, "rsi_buy_max": 70.0, "rsi_sell_min": 30.0, "rsi_sell_max": 50.0},
    "mean_reversion_bbands": {"require_oscillator_confirmation": True},
    "breakout_structure": {"bullish_close_strength": 0.70, "bearish_close_strength": 0.30},
    "scalping_m5": {"require_volume_or_pattern": True},
    "swing_h1_h4": {"require_structure_bias": True},
}


def normalize_strategy_params(strategy_key: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    """Validate the deliberately small, bounded parameter surface used in research replay."""
    if strategy_key not in STRATEGY_PARAMETER_DEFAULTS:
        return {"ok": False, "error": "unsupported_strategy_parameter_set"}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return {"ok": False, "error": "strategy_params_must_be_an_object"}
    defaults = STRATEGY_PARAMETER_DEFAULTS[strategy_key]
    if any(key not in defaults for key in raw):
        return {"ok": False, "error": "unsupported_strategy_parameter"}
    result = dict(defaults)
    for key, value in raw.items():
        if isinstance(defaults[key], bool):
            if not isinstance(value, bool):
                return {"ok": False, "error": f"invalid_strategy_parameter_{key}"}
            result[key] = value
        elif key == "min_confirmations":
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 4:
                return {"ok": False, "error": "invalid_strategy_parameter_min_confirmations"}
            result[key] = value
        else:
            if isinstance(value, bool):
                return {"ok": False, "error": f"invalid_strategy_parameter_{key}"}
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                return {"ok": False, "error": f"invalid_strategy_parameter_{key}"}
            if not math.isfinite(number):
                return {"ok": False, "error": f"invalid_strategy_parameter_{key}"}
            rsi_bounds = {
                "rsi_buy_min": (40.0, 65.0), "rsi_buy_max": (55.0, 85.0),
                "rsi_sell_min": (15.0, 45.0), "rsi_sell_max": (35.0, 60.0),
            }
            if key in rsi_bounds and not rsi_bounds[key][0] <= number <= rsi_bounds[key][1]:
                return {"ok": False, "error": f"strategy_parameter_out_of_range_{key}"}
            if key == "bullish_close_strength" and not 0.5 <= number <= 1:
                return {"ok": False, "error": f"strategy_parameter_out_of_range_{key}"}
            if key == "bearish_close_strength" and not 0 <= number <= 0.5:
                return {"ok": False, "error": f"strategy_parameter_out_of_range_{key}"}
            result[key] = number
    if strategy_key == "momentum_macd_rsi" and (result["rsi_buy_min"] >= result["rsi_buy_max"] or result["rsi_sell_min"] >= result["rsi_sell_max"]):
        return {"ok": False, "error": "strategy_rsi_parameter_ranges_must_be_ordered"}
    if strategy_key == "breakout_structure" and result["bearish_close_strength"] >= result["bullish_close_strength"]:
        return {"ok": False, "error": "strategy_breakout_strength_ranges_must_be_ordered"}
    return {"ok": True, "params": result}


@dataclass
class BacktestResult:
    total_trades: int
    win_rate: float
    profit_factor: float
    expectancy: float
    max_drawdown: float
    total_return: float
    fees_paid: float
    slippage_cost: float
    sharpe: float | None


class BacktestEngine:
    def __init__(
        self,
        fee_rate: float = 0.001,
        slippage_rate: float = 0.0005,
        initial_cash: float = 10_000.0,
        allocation_fraction: float = 0.95,
        max_notional: float = 80.0,
    ):
        if any(isinstance(value, bool) for value in (fee_rate, slippage_rate, initial_cash, allocation_fraction, max_notional)):
            raise ValueError("boolean_backtest_configuration_not_allowed")
        try:
            fee_rate = float(fee_rate)
            slippage_rate = float(slippage_rate)
            initial_cash = float(initial_cash)
            allocation_fraction = float(allocation_fraction)
            max_notional = float(max_notional)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("invalid_backtest_numeric_configuration")
        if not all(math.isfinite(value) for value in (fee_rate, slippage_rate, initial_cash, allocation_fraction, max_notional)):
            raise ValueError("non_finite_backtest_configuration")
        if not 0 <= fee_rate < 1 or not 0 <= slippage_rate < 1:
            raise ValueError("fee_and_slippage_must_be_between_zero_and_one")
        if initial_cash <= 0 or not 0 < allocation_fraction <= 1 or max_notional <= 0:
            raise ValueError("invalid_backtest_capital_or_allocation")
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.initial_cash = initial_cash
        self.allocation_fraction = allocation_fraction
        self.max_notional = max_notional

    def run(self, candles: list[dict], signals: list[dict], *, pyramiding_enabled: bool = False, max_adds: int = 2, add_fraction: float = 0.25) -> dict[str, Any]:
        if not isinstance(candles, list) or not isinstance(signals, list):
            return self._empty("candles_and_signals_must_be_lists")
        if not isinstance(pyramiding_enabled, bool) or isinstance(max_adds, bool) or not isinstance(max_adds, int):
            return self._empty("invalid_pyramiding_configuration")
        if isinstance(add_fraction, bool):
            return self._empty("boolean_pyramiding_fraction_not_allowed")
        try:
            add_fraction = float(add_fraction)
        except (TypeError, ValueError, OverflowError):
            return self._empty("invalid_pyramiding_fraction")
        if not math.isfinite(add_fraction) or not 0 < add_fraction <= 1 or not 1 <= max_adds <= 5:
            return self._empty("pyramiding_configuration_out_of_range")
        if len(candles) < 2:
            return self._empty("at_least_two_candles_required")
        normalized = []
        for i, candle in enumerate(candles):
            if not isinstance(candle, dict) or any(isinstance(candle.get(key), bool) for key in ("open", "high", "low", "close")):
                return self._empty(f"invalid_ohlc_at_index_{i}")
            try:
                o, h, l, c = (float(candle[k]) for k in ("open", "high", "low", "close"))
            except (KeyError, TypeError, ValueError):
                return self._empty(f"invalid_ohlc_at_index_{i}")
            if (not all(math.isfinite(value) for value in (o, h, l, c))
                    or min(o, h, l, c) <= 0 or h < max(o, c, l) or l > min(o, c, h)):
                return self._empty(f"invalid_ohlc_at_index_{i}")
            normalized.append({"open": o, "high": h, "low": l, "close": c})

        by_index: dict[int, list[dict[str, Any]]] = {}
        for sig in signals:
            if not isinstance(sig, dict):
                return self._empty("signals_must_be_objects")
            action = str(sig.get("action", "")).lower()
            if action not in {"buy", "sell", "add"}:
                continue
            idx = sig.get("index", sig.get("candle_index"))
            if idx is None:
                return self._empty("each_signal_requires_candle_index")
            if isinstance(idx, bool):
                return self._empty("invalid_signal_index")
            try:
                numeric_index = float(idx)
                if not math.isfinite(numeric_index) or not numeric_index.is_integer():
                    return self._empty("invalid_signal_index")
                idx = int(numeric_index)
            except (TypeError, ValueError, OverflowError):
                return self._empty("invalid_signal_index")
            if idx < 0 or idx >= len(normalized) - 1:
                continue
            normalized_signal = dict(sig)
            normalized_signal["action"] = action
            for key in ("stop_distance", "target_distance"):
                if key in normalized_signal:
                    value = normalized_signal[key]
                    if isinstance(value, bool):
                        return self._empty("boolean_protective_exit_distance_not_allowed")
                    try:
                        value = float(value)
                    except (TypeError, ValueError, OverflowError):
                        return self._empty("invalid_protective_exit_distance")
                    if not math.isfinite(value) or value <= 0:
                        return self._empty("protective_exit_distance_out_of_range")
                    normalized_signal[key] = value
            by_index.setdefault(idx, []).append(normalized_signal)
        if any(any(sig["action"] == "buy" for sig in rows)
               and any(sig["action"] == "sell" for sig in rows) for rows in by_index.values()):
            return self._empty("conflicting_signals_same_candle")

        cash = self.initial_cash
        qty = 0.0
        entry_price = None
        entry_fee = 0.0
        entry_slippage = 0.0
        entry_index = None
        entry_notional = 0.0
        base_entry_notional = 0.0
        add_count = 0
        add_entries: list[dict[str, Any]] = []
        trades: list[dict[str, Any]] = []
        fees_total = 0.0
        slippage_total = 0.0
        equity_curve = [self.initial_cash]
        pending_exit_reason = None
        stop_price = None
        target_price = None
        stop_distance = None
        target_distance = None

        def mark_equity(mark: float) -> float:
            return cash + qty * mark

        for i in range(len(normalized) - 1):
            # Signals at candle i execute at candle i+1 open, never at the signal candle close.
            next_open = normalized[i + 1]["open"]
            signal_rows = by_index.get(i, [])
            actions = [row["action"] for row in signal_rows]
            if "sell" in actions and qty > 0:
                fill = next_open * (1 - self.slippage_rate)
                proceeds = qty * fill
                exit_fee = proceeds * self.fee_rate
                cash += proceeds - exit_fee
                fees_total += exit_fee
                slippage_total += qty * abs(next_open - fill)
                gross = qty * (fill - float(entry_price))
                net = gross - entry_fee - exit_fee
                trades.append({"entry_index": entry_index, "exit_index": i + 1, "entry_price": entry_price,
                               "exit_price": fill, "quantity": qty, "gross_pnl": gross,
                               "fees": entry_fee + exit_fee, "net_pnl": net, "exit_reason": "sell_signal",
                               "add_count": add_count, "add_entries": list(add_entries)})
                qty = 0.0; entry_price = None; entry_fee = 0.0; entry_index = None
                entry_notional = 0.0; base_entry_notional = 0.0; add_count = 0; add_entries = []
                stop_price = target_price = stop_distance = target_distance = None
            elif "buy" in actions and qty <= 0:
                signal = next(row for row in signal_rows if row["action"] == "buy")
                budget_cap = self.max_notional / (1.0 + max_adds * add_fraction) if pyramiding_enabled else self.max_notional
                budget = min(cash * self.allocation_fraction, budget_cap)
                fill = next_open * (1 + self.slippage_rate)
                buy_qty = budget / (fill * (1 + self.fee_rate))
                notional = buy_qty * fill
                fee = notional * self.fee_rate
                cash -= notional + fee
                qty = buy_qty
                entry_price = fill
                entry_fee = fee
                entry_index = i + 1
                entry_notional = notional
                base_entry_notional = notional
                add_count = 0
                add_entries = []
                stop_distance = signal.get("stop_distance")
                target_distance = signal.get("target_distance")
                stop_price = fill - stop_distance if stop_distance is not None else None
                target_price = fill + target_distance if target_distance is not None else None
                fees_total += fee
                slippage_total += buy_qty * abs(next_open - fill)
            elif "add" in actions and qty > 0 and pyramiding_enabled and add_count < max_adds:
                remaining_cap = max(0.0, self.max_notional - entry_notional)
                budget = min(base_entry_notional * add_fraction, remaining_cap, cash * self.allocation_fraction)
                fill = next_open * (1 + self.slippage_rate)
                buy_qty = budget / (fill * (1 + self.fee_rate)) if budget > 0 else 0.0
                notional = buy_qty * fill
                fee = notional * self.fee_rate
                if buy_qty > 0 and notional + fee <= cash + 1e-9:
                    previous_qty = qty
                    qty += buy_qty
                    entry_price = ((float(entry_price) * previous_qty) + (fill * buy_qty)) / qty
                    entry_fee += fee
                    entry_notional += notional
                    add_count += 1
                    add_entries.append({"entry_index": i + 1, "entry_price": fill,
                                        "quantity": buy_qty, "notional": notional})
                    if stop_distance is not None:
                        stop_price = entry_price - stop_distance
                    if target_distance is not None:
                        target_price = entry_price + target_distance
                    cash -= notional + fee
                    fees_total += fee
                    slippage_total += buy_qty * abs(next_open - fill)

            # Evaluate protective levels against the next candle's OHLC. If both
            # levels are touched in one candle, assume the stop is hit first.
            if qty > 0 and (stop_price is not None or target_price is not None):
                next_candle = normalized[i + 1]
                stop_hit = stop_price is not None and next_candle["low"] <= stop_price
                target_hit = target_price is not None and next_candle["high"] >= target_price
                if stop_hit or target_hit:
                    if stop_hit:
                        raw_exit = next_open if next_open <= stop_price else stop_price
                        exit_reason = "stop_loss"
                    else:
                        raw_exit = target_price
                        exit_reason = "take_profit"
                    fill = raw_exit * (1 - self.slippage_rate)
                    proceeds = qty * fill
                    exit_fee = proceeds * self.fee_rate
                    cash += proceeds - exit_fee
                    fees_total += exit_fee
                    slippage_total += qty * abs(raw_exit - fill)
                    gross = qty * (fill - float(entry_price))
                    net = gross - entry_fee - exit_fee
                    trades.append({"entry_index": entry_index, "exit_index": i + 1, "entry_price": entry_price,
                                   "exit_price": fill, "quantity": qty, "gross_pnl": gross,
                                   "fees": entry_fee + exit_fee, "net_pnl": net, "exit_reason": exit_reason,
                                   "add_count": add_count, "add_entries": list(add_entries)})
                    qty = 0.0; entry_price = None; entry_fee = 0.0; entry_index = None
                    entry_notional = 0.0; base_entry_notional = 0.0; add_count = 0; add_entries = []
                    stop_price = target_price = stop_distance = target_distance = None
            equity_curve.append(mark_equity(normalized[i + 1]["close"]))

        if qty > 0:
            last_index = len(normalized) - 1
            raw_exit = normalized[-1]["close"]
            fill = raw_exit * (1 - self.slippage_rate)
            proceeds = qty * fill
            exit_fee = proceeds * self.fee_rate
            cash += proceeds - exit_fee
            fees_total += exit_fee
            slippage_total += qty * abs(raw_exit - fill)
            gross = qty * (fill - float(entry_price))
            net = gross - entry_fee - exit_fee
            trades.append({"entry_index": entry_index, "exit_index": last_index, "entry_price": entry_price,
                           "exit_price": fill, "quantity": qty, "gross_pnl": gross,
                           "fees": entry_fee + exit_fee, "net_pnl": net, "exit_reason": "end_of_data",
                           "add_count": add_count, "add_entries": list(add_entries)})
            qty = 0.0
            equity_curve[-1] = cash

        pnl_values = [t["net_pnl"] for t in trades]
        wins = [p for p in pnl_values if p > 0]
        losses = [p for p in pnl_values if p < 0]
        gross_profit, gross_loss = sum(wins), abs(sum(losses))
        peak = self.initial_cash
        max_dd = 0.0
        for value in equity_curve:
            peak = max(peak, value)
            max_dd = max(max_dd, (peak - value) / peak if peak else 0.0)
        returns = [(equity_curve[i] / equity_curve[i - 1] - 1) for i in range(1, len(equity_curve)) if equity_curve[i - 1] > 0]
        sharpe = None
        if len(returns) > 1:
            mean = sum(returns) / len(returns)
            variance = sum((x - mean) ** 2 for x in returns) / (len(returns) - 1)
            if variance > 0:
                sharpe = (mean / math.sqrt(variance)) * math.sqrt(252)
        final_equity = cash
        return {
            "ok": True,
            "total_trades": len(trades),
            "win_rate": round(len(wins) / len(trades), 4) if trades else 0.0,
            "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else (None if gross_profit == 0 else "infinite"),
            "expectancy": round(sum(pnl_values) / len(pnl_values), 8) if trades else 0.0,
            "max_drawdown": round(max_dd, 6),
            "total_return": round((final_equity - self.initial_cash) / self.initial_cash, 6),
            "final_equity": round(final_equity, 8),
            "fees_paid": round(fees_total, 8),
            "slippage_cost": round(slippage_total, 8),
            "sharpe": round(sharpe, 4) if sharpe is not None else None,
            "trades": trades,
            "equity_curve": [round(x, 8) for x in equity_curve],
            "execution_model": "next_candle_open_with_fee_and_slippage",
            "position_mode": "spot_long_only_single_net_position_with_bounded_pyramiding" if pyramiding_enabled else "spot_long_only_single_position",
            "pyramiding_enabled": pyramiding_enabled,
            "max_adds": max_adds if pyramiding_enabled else 0,
            "add_fraction_of_initial_entry": add_fraction if pyramiding_enabled else 0.0,
            "max_trade_notional": self.max_notional,
            "walk_forward_ready": False,
            "out_of_sample_ready": False,
            "no_lookahead": True,
            "limitations": ["single position", "signal index required", "no partial fills or exchange filters",
                            "end-of-data liquidation", "not a walk-forward or out-of-sample validation"],
        }

    def walk_forward(
        self,
        candles: list[dict],
        *,
        signal_fn,
        train_window: int,
        test_window: int,
        step: int | None = None,
        fit_fn=None,
    ) -> dict[str, Any]:
        """Run rolling out-of-sample folds without exposing future candles.

        fit_fn receives only the training slice. signal_fn receives the training
        slice plus the test history available up to the current test candle.
        Signals fill at the next test candle open through the normal simulator.
        """
        if not callable(signal_fn):
            return self._empty("signal_fn_must_be_callable")
        if fit_fn is not None and not callable(fit_fn):
            return self._empty("fit_fn_must_be_callable")
        for name, value in (("train_window", train_window), ("test_window", test_window)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                return self._empty(f"{name}_must_be_an_integer_at_least_2")
        step = test_window if step is None else step
        if isinstance(step, bool) or not isinstance(step, int) or step < test_window:
            return self._empty("step_must_be_at_least_test_window_to_avoid_overlapping_oos_folds")
        if len(candles) < train_window + test_window:
            return self._empty("insufficient_candles_for_train_and_test_windows")

        folds: list[dict[str, Any]] = []
        start = 0
        fold_number = 0
        while start + train_window + test_window <= len(candles):
            train_start = start
            train_end = start + train_window
            test_end = train_end + test_window
            training = [dict(row) for row in candles[train_start:train_end]]
            testing = [dict(row) for row in candles[train_end:test_end]]
            fold_number += 1
            try:
                model_state = fit_fn(training) if fit_fn is not None else None
                signals: list[dict[str, Any]] = []
                # The last test candle cannot emit an actionable signal because
                # there is no next candle open inside this fold to fill it.
                for local_index in range(len(testing) - 1):
                    history_end = train_end + local_index + 1
                    visible_history = [
                        dict(row) for row in candles[train_start:history_end]
                    ]
                    generated = signal_fn(visible_history, model_state)
                    action = generated.get("action") if isinstance(generated, dict) else generated
                    action = str(action or "").lower()
                    if action in {"buy", "sell"}:
                        signals.append({"index": local_index, "action": action})
            except Exception as exc:
                return {
                    "ok": False,
                    "error": f"walk_forward_callback_failed_fold_{fold_number}",
                    "detail": str(exc)[:240],
                    "folds": folds,
                    "walk_forward_ready": False,
                    "out_of_sample_ready": False,
                    "no_lookahead": False,
                }

            result = self.run(testing, signals)
            if result.get("ok") is False:
                return {
                    "ok": False,
                    "error": f"walk_forward_test_fold_failed_{fold_number}",
                    "detail": result.get("error"),
                    "folds": folds,
                    "walk_forward_ready": False,
                    "out_of_sample_ready": False,
                    "no_lookahead": False,
                }
            folds.append({
                "fold": fold_number,
                "train_start_index": train_start,
                "train_end_index_exclusive": train_end,
                "test_start_index": train_end,
                "test_end_index_exclusive": test_end,
                "training_candles": len(training),
                "testing_candles": len(testing),
                "model_fitted": fit_fn is not None,
                "result": result,
            })
            start += step

        if not folds:
            return self._empty("no_walk_forward_folds_created")
        all_trades = [trade for fold in folds for trade in fold["result"]["trades"]]
        wins = [trade["net_pnl"] for trade in all_trades if trade["net_pnl"] > 0]
        losses = [trade["net_pnl"] for trade in all_trades if trade["net_pnl"] < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        compounded_return = 1.0
        for fold in folds:
            compounded_return *= 1.0 + float(fold["result"]["total_return"])
        return {
            "ok": True,
            "validation": "rolling_walk_forward_out_of_sample",
            "fold_count": len(folds),
            "total_trades": len(all_trades),
            "win_rate": round(len(wins) / len(all_trades), 4) if all_trades else 0.0,
            "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else (None if gross_profit == 0 else "infinite"),
            "expectancy": round(sum(t["net_pnl"] for t in all_trades) / len(all_trades), 8) if all_trades else 0.0,
            "compounded_oos_return": round(compounded_return - 1.0, 6),
            "mean_fold_return": round(sum(f["result"]["total_return"] for f in folds) / len(folds), 6),
            "max_fold_drawdown": round(max(f["result"]["max_drawdown"] for f in folds), 6),
            "fees_paid": round(sum(f["result"]["fees_paid"] for f in folds), 8),
            "slippage_cost": round(sum(f["result"]["slippage_cost"] for f in folds), 8),
            "folds": folds,
            "walk_forward_ready": True,
            "out_of_sample_ready": True,
            "no_lookahead": True,
            "model_fitted_per_fold": fit_fn is not None,
            "signal_generation": "signal_fn_sees_training_window_and_test_history_up_to_current_candle_only",
            "limitations": [
                "each out-of-sample fold starts with a fresh initial cash balance",
                "fold returns are compounded for the aggregate metric",
                "single-position Spot-long simulator; no partial fills or exchange filters",
                "signal and fit callbacks must themselves avoid external future-data leakage",
            ],
        }

    @staticmethod
    def _strategy_action(strategy_key: str, analysis, params: dict[str, Any] | None = None) -> str | None:
        """Strategy-specific closed-candle rules shared by Spot and Futures replays."""
        params = params if isinstance(params, dict) else STRATEGY_PARAMETER_DEFAULTS.get(strategy_key, {})
        signals = set(getattr(analysis, "signals", []) or [])
        indicators = getattr(analysis, "indicators", {}) or {}
        structure = getattr(analysis, "structure", {}) or {}
        candles = getattr(analysis, "candles", {}) or {}
        timeframe = getattr(getattr(analysis, "timeframe", None), "value",
                             str(getattr(analysis, "timeframe", "")))

        def number(key):
            value = indicators.get(key)
            if isinstance(value, bool):
                return None
            try:
                result = float(value)
                return result if math.isfinite(result) else None
            except (TypeError, ValueError, OverflowError):
                return None

        if strategy_key == "trend_following_ema":
            if analysis.trend == "bullish":
                confirmations = sum((
                    "ema9_above_ema21" in signals,
                    "supertrend_bullish" in signals,
                    "price_above_ema20_ema50" in signals,
                    "price_above_ema200" in signals,
                ))
                if confirmations >= params.get("min_confirmations", 2):
                    return "buy"
            elif analysis.trend == "bearish":
                confirmations = sum((
                    "ema9_below_ema21" in signals,
                    "supertrend_bearish" in signals,
                    "price_below_ema20_ema50" in signals,
                    "price_below_ema200" in signals,
                ))
                if confirmations >= 2:
                    return "sell"

        elif strategy_key == "momentum_macd_rsi":
            rsi = number("rsi14")
            stoch = number("stoch_rsi_k")
            cci = number("cci20")
            if "macd_hist_positive" in signals and "rsi_overbought" not in signals:
                oscillator_ok = (
                    (rsi is not None and params.get("rsi_buy_min", 50.0) <= rsi <= params.get("rsi_buy_max", 70.0))
                    or (stoch is not None and 50 <= stoch <= 80)
                    or (cci is not None and cci > 0)
                )
                if oscillator_ok:
                    return "buy"
            if "macd_hist_negative" in signals and "rsi_oversold" not in signals:
                oscillator_ok = (
                    (rsi is not None and params.get("rsi_sell_min", 30.0) <= rsi <= params.get("rsi_sell_max", 50.0))
                    or (stoch is not None and 20 <= stoch <= 50)
                    or (cci is not None and cci < 0)
                )
                if oscillator_ok:
                    return "sell"

        elif strategy_key == "mean_reversion_bbands":
            if ("rsi_oversold" in signals and "bollinger_lower_band_touch" in signals
                    and (not params.get("require_oscillator_confirmation", True)
                         or "cci_oversold" in signals or "stoch_rsi_oversold" in signals)):
                return "buy"
            if ("rsi_overbought" in signals and "bollinger_upper_band_touch" in signals
                    and (not params.get("require_oscillator_confirmation", True)
                         or "cci_overbought" in signals or "stoch_rsi_overbought" in signals)):
                return "sell"

        elif strategy_key == "breakout_structure":
            confirmed = [
                item for item in structure.get("breakouts", [])
                if isinstance(item, dict) and item.get("status") == "confirmed"
            ]
            patterns = set(candles.get("patterns", []) or [])
            close_strength = candles.get("close_strength")
            try:
                close_strength = float(close_strength)
                if not math.isfinite(close_strength):
                    close_strength = None
            except (TypeError, ValueError, OverflowError):
                close_strength = None
            for breakout in confirmed:
                direction = breakout.get("direction")
                if direction == "bullish" and (
                    "volume_spike" in signals or close_strength is not None and close_strength >= params.get("bullish_close_strength", 0.70)
                    or bool({"bullish_engulfing", "morning_star", "pin_bar_bullish"}.intersection(patterns))
                ):
                    return "buy"
                if direction == "bearish" and (
                    "volume_spike" in signals or close_strength is not None and close_strength <= params.get("bearish_close_strength", 0.30)
                    or bool({"bearish_engulfing", "evening_star", "pin_bar_bearish"}.intersection(patterns))
                ):
                    return "sell"
            # Reversal evidence: confirmed liquidity sweep + still-open FVG.
            # A counter-trend reversal additionally requires a same-direction CHOCH.
            sweeps = structure.get("liquidity_sweeps", []) or []
            gaps = structure.get("fair_value_gaps", []) or []
            choch = structure.get("bos_choch", []) or []
            regime = (structure.get("market_regime", {}) or {}).get("trend", "unknown")
            for direction, action, minimum_strength, opposing_regime in (
                ("bullish", "buy", 0.65, "trending_bearish"),
                ("bearish", "sell", 0.35, "trending_bullish"),
            ):
                has_sweep = any(isinstance(x,dict) and x.get("direction")==direction for x in sweeps)
                has_gap = any(isinstance(x,dict) and x.get("direction")==direction and x.get("status")=="open" for x in gaps)
                has_choch = any(isinstance(x,dict) and x.get("type")=="CHOCH" and x.get("direction")==direction for x in choch)
                if (has_sweep and has_gap and close_strength is not None
                        and ((direction=="bullish" and close_strength>=minimum_strength)
                             or (direction=="bearish" and close_strength<=minimum_strength))
                        and (regime != opposing_regime or has_choch)):
                    return action

        elif strategy_key == "scalping_m5":
            if timeframe != "5m":
                return None
            bullish_confirm = {"bullish_engulfing", "morning_star", "pin_bar_bullish", "hammer"}
            bearish_confirm = {"bearish_engulfing", "evening_star", "pin_bar_bearish", "shooting_star"}
            patterns = set(candles.get("patterns", []) or [])
            if (analysis.trend == "bullish" and "macd_hist_positive" in signals
                    and "rsi_overbought" not in signals
                    and (not params.get("require_volume_or_pattern", True) or "volume_spike" in signals or bullish_confirm.intersection(patterns))
                    and ("ema9_above_ema21" in signals or "supertrend_bullish" in signals)):
                return "buy"
            if (analysis.trend == "bearish" and "macd_hist_negative" in signals
                    and "rsi_oversold" not in signals
                    and (not params.get("require_volume_or_pattern", True) or "volume_spike" in signals or bearish_confirm.intersection(patterns))
                    and ("ema9_below_ema21" in signals or "supertrend_bearish" in signals)):
                return "sell"

        elif strategy_key == "swing_h1_h4":
            if timeframe not in {"1h", "4h"}:
                return None
            bias = structure.get("bias")
            if (analysis.trend == "bullish" and (not params.get("require_structure_bias", True) or bias == "bullish_structure")
                    and "macd_hist_positive" in signals
                    and ("supertrend_bullish" in signals or "price_above_ichimoku_cloud" in signals)):
                return "buy"
            if (analysis.trend == "bearish" and (not params.get("require_structure_bias", True) or bias == "bearish_structure")
                    and "macd_hist_negative" in signals
                    and ("supertrend_bearish" in signals or "price_below_ichimoku_cloud" in signals)):
                return "sell"
        return None

    def run_strategy(
        self,
        candles: list[dict],
        *,
        strategy_key: str,
        timeframe,
        pyramiding_enabled: bool = False,
        max_adds: int = 2,
        add_fraction: float = 0.25,
        add_trigger_pct: float = 0.01,
        strategy_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Causal historical backtest using the existing TechnicalAnalysisEngine."""
        allowed = {"trend_following_ema", "momentum_macd_rsi", "mean_reversion_bbands", "breakout_structure", "scalping_m5", "swing_h1_h4"}
        if strategy_key not in allowed:
            return self._empty("unsupported_or_not_implemented_strategy")
        normalized_params = normalize_strategy_params(strategy_key, strategy_params)
        if not normalized_params.get("ok"):
            return self._empty(normalized_params.get("error", "invalid_strategy_params"))
        params = normalized_params["params"]
        if len(candles) < 60:
            return self._empty("at_least_60_candles_required")
        try:
            from app.core.enums import Timeframe
            from app.engines.market_data import Candles
            from app.engines.technical import TechnicalAnalysisEngine
            tf = timeframe if isinstance(timeframe, Timeframe) else Timeframe(str(timeframe))
            if strategy_key == "scalping_m5" and tf.value != "5m":
                return self._empty("scalping_m5_requires_5m_timeframe")
            if strategy_key == "swing_h1_h4" and tf.value not in {"1h", "4h"}:
                return self._empty("swing_h1_h4_requires_1h_or_4h_timeframe")
            if not isinstance(pyramiding_enabled, bool) or isinstance(max_adds, bool) or not isinstance(max_adds, int):
                return self._empty("invalid_pyramiding_configuration")
            if any(isinstance(value, bool) for value in (add_fraction, add_trigger_pct)):
                return self._empty("boolean_pyramiding_configuration_not_allowed")
            add_fraction, add_trigger_pct = float(add_fraction), float(add_trigger_pct)
            if not math.isfinite(add_fraction) or not math.isfinite(add_trigger_pct) or not 0 < add_fraction <= 1 or not 0 < add_trigger_pct <= 0.1 or not 1 <= max_adds <= 5:
                return self._empty("pyramiding_configuration_out_of_range")
            technical = TechnicalAnalysisEngine()
            signals = []
            active = False
            adds = 0
            last_add_reference = 0.0
            pending_entry_atr = None
            active_stop = None
            active_target = None
            for index in range(49, len(candles) - 1):
                risk_exit_this_candle = False
                if pending_entry_atr is not None:
                    # The prior candle's signal fills at this candle's open.
                    entry_fill = float(candles[index]["open"])
                    active_stop = entry_fill - pending_entry_atr * 1.5
                    active_target = entry_fill + pending_entry_atr * 2.5
                    active = True
                    pending_entry_atr = None
                if active:
                    low_price = float(candles[index]["low"])
                    high_price = float(candles[index]["high"])
                    stop_hit = active_stop is not None and low_price <= active_stop
                    target_hit = active_target is not None and high_price >= active_target
                    if stop_hit or target_hit:
                        active = False
                        adds = 0
                        active_stop = active_target = None
                        last_add_reference = 0.0
                        risk_exit_this_candle = True
                history = Candles(candles[: index + 1])
                analysis = technical.analyze(history, tf)
                action = self._strategy_action(strategy_key, analysis, params)
                close_price = float(candles[index]["close"])
                if action == "buy":
                    if not active and not risk_exit_this_candle:
                        atr = float(analysis.indicators.get("atr14") or 0.0)
                        signal = {"index": index, "action": "buy"}
                        if math.isfinite(atr) and atr > 0:
                            signal["stop_distance"] = atr * 1.5
                            signal["target_distance"] = atr * 2.5
                            pending_entry_atr = atr
                        signals.append(signal)
                        adds, last_add_reference = 0, close_price
                    elif active and pyramiding_enabled and adds < max_adds and close_price >= last_add_reference * (1.0 + add_trigger_pct):
                        signals.append({"index": index, "action": "add"})
                        adds += 1
                        last_add_reference = close_price
                elif action == "sell" and active:
                    signals.append({"index": index, "action": "sell"})
                    active, adds, last_add_reference = False, 0, 0.0
                    pending_entry_atr = None
                    active_stop = active_target = None
            result = self.run(candles, signals, pyramiding_enabled=pyramiding_enabled,
                              max_adds=max_adds, add_fraction=add_fraction)
            result.update({
                "strategy_key": strategy_key,
                "strategy_params": params,
                "timeframe": tf.value,
                "signal_generation": "technical_analysis_on_causal_history_prefix",
                "signal_rules_version": "strategy_confirmation_v2",
                "pyramiding_enabled": pyramiding_enabled,
                "max_adds": max_adds if pyramiding_enabled else 0,
                "add_trigger_pct": add_trigger_pct if pyramiding_enabled else 0.0,
                "walk_forward_ready": False,
                "out_of_sample_ready": False,
                "no_lookahead": True,
                "protective_exit_model": "intrabar_ohlc_stop_first_when_both_levels_hit",
            })
            return result
        except Exception as exc:
            return self._empty("strategy_backtest_failed:" + str(exc)[:160])

    def run_strategy_walk_forward(
        self,
        candles: list[dict],
        *,
        strategy_key: str,
        timeframe,
        train_window: int = 60,
        test_window: int = 30,
        step: int | None = None,
        strategy_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Rolling OOS backtest; strategy sees only each fold's past/current candles."""
        allowed = {"trend_following_ema", "momentum_macd_rsi", "mean_reversion_bbands", "breakout_structure", "scalping_m5", "swing_h1_h4"}
        if strategy_key not in allowed:
            return self._empty("unsupported_or_not_implemented_strategy")
        normalized_params = normalize_strategy_params(strategy_key, strategy_params)
        if not normalized_params.get("ok"):
            return self._empty(normalized_params.get("error", "invalid_strategy_params"))
        params = normalized_params["params"]
        if isinstance(train_window, bool) or not isinstance(train_window, int) or train_window < 50:
            return self._empty("train_window_must_be_at_least_50")
        try:
            from app.core.enums import Timeframe
            from app.engines.market_data import Candles
            from app.engines.technical import TechnicalAnalysisEngine
            tf = timeframe if isinstance(timeframe, Timeframe) else Timeframe(str(timeframe))
            if strategy_key == "scalping_m5" and tf.value != "5m":
                return self._empty("scalping_m5_requires_5m_timeframe")
            if strategy_key == "swing_h1_h4" and tf.value not in {"1h", "4h"}:
                return self._empty("swing_h1_h4_requires_1h_or_4h_timeframe")
            technical = TechnicalAnalysisEngine()

            def signal_fn(history: list[dict], model_state):
                if len(history) < 50:
                    return "wait"
                analysis = technical.analyze(Candles(history), tf)
                return self._strategy_action(strategy_key, analysis, params) or "wait"

            result = self.walk_forward(
                candles, signal_fn=signal_fn, train_window=train_window,
                test_window=test_window, step=step,
            )
            result.update({
                "strategy_key": strategy_key,
                "strategy_params": params,
                "timeframe": tf.value,
                "model_fitted_per_fold": False,
                "signal_generation": "existing_technical_strategy_on_causal_fold_history",
                "note": "Deterministic strategy replay; no parameter fitting occurs. The training window supplies past context only.",
            })
            return result
        except Exception as exc:
            return self._empty("strategy_walk_forward_failed:" + str(exc)[:160])

    def _empty(self, reason: str) -> dict[str, Any]:
        return {
            "ok": False, "error": reason, "total_trades": 0, "win_rate": 0.0,
            "profit_factor": 0.0, "expectancy": 0.0, "max_drawdown": 0.0,
            "total_return": 0.0, "fees_paid": 0.0, "slippage_cost": 0.0,
            "sharpe": None, "trades": [], "equity_curve": [self.initial_cash],
            "walk_forward_ready": False, "out_of_sample_ready": False, "no_lookahead": False,
        }
