"""Isolated-margin Futures backtest with funding, partial fills and liquidation estimates.

This is a conservative research simulator, not an exchange-matching engine.
It models one position at a time and does not submit orders.
"""
from __future__ import annotations

import math
from typing import Any, Sequence


class FuturesBacktestEngine:
    def __init__(
        self,
        *,
        initial_cash: float = 10_000.0,
        fee_rate: float = 0.0004,
        slippage_rate: float = 0.0005,
        allocation_fraction: float = 0.5,
        max_notional: float = 800.0,
        max_leverage: float = 10.0,
        maintenance_margin_rate: float = 0.005,
        liquidation_fee_rate: float = 0.005,
    ) -> None:
        raw = (initial_cash, fee_rate, slippage_rate, allocation_fraction, max_notional,
               max_leverage, maintenance_margin_rate, liquidation_fee_rate)
        if any(isinstance(value, bool) for value in raw):
            raise ValueError("boolean_futures_backtest_configuration_not_allowed")
        try:
            values = [float(value) for value in raw]
        except (TypeError, ValueError, OverflowError):
            raise ValueError("invalid_futures_backtest_configuration")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("non_finite_futures_backtest_configuration")
        (self.initial_cash, self.fee_rate, self.slippage_rate, self.allocation_fraction,
         self.max_notional, self.max_leverage, self.maintenance_margin_rate,
         self.liquidation_fee_rate) = values
        if (self.initial_cash <= 0 or not 0 <= self.fee_rate < 1
                or not 0 <= self.slippage_rate < 1
                or not 0 < self.allocation_fraction <= 1
                or self.max_notional <= 0 or self.max_leverage < 1
                or not 0 < self.maintenance_margin_rate < 0.5
                or not 0 <= self.liquidation_fee_rate < 1):
            raise ValueError("futures_backtest_configuration_out_of_range")

    @staticmethod
    def _empty(reason: str, initial_cash: float) -> dict[str, Any]:
        return {
            "ok": False, "error": reason, "total_trades": 0, "win_rate": 0.0,
            "total_return": 0.0, "final_equity": round(initial_cash, 8),
            "fees_paid": 0.0, "slippage_cost": 0.0, "funding_net": 0.0,
            "liquidations": 0, "partial_fills": 0, "trades": [],
            "equity_curve": [round(initial_cash, 8)], "no_lookahead": False,
            "signal_causality": "not_verified_for_external_signals",
        }

    def run_strategy(
        self,
        candles: list[dict],
        *,
        strategy_key: str,
        timeframe,
        funding_events: Sequence[dict] = (),
        leverage: float = 1.0,
        notional: float | None = None,
        entry_fill_ratio: float = 1.0,
        exit_fill_ratio: float = 1.0,
        pyramiding_enabled: bool = False,
        max_adds: int = 2,
        add_fraction: float = 0.25,
        add_trigger_pct: float = 0.01,
    ) -> dict[str, Any]:
        allowed = {
            "trend_following_ema", "momentum_macd_rsi", "mean_reversion_bbands",
            "breakout_structure", "scalping_m5", "swing_h1_h4",
        }
        if strategy_key not in allowed:
            return self._empty("unsupported_or_not_implemented_strategy", self.initial_cash)
        if any(isinstance(value, bool) for value in (leverage, notional, entry_fill_ratio, exit_fill_ratio) if value is not None):
            return self._empty("boolean_strategy_configuration_not_allowed", self.initial_cash)
        try:
            leverage_value = float(leverage)
            entry_ratio = float(entry_fill_ratio)
            exit_ratio = float(exit_fill_ratio)
            notional_value = float(notional) if notional is not None else None
            if (not math.isfinite(leverage_value) or leverage_value < 1 or leverage_value > self.max_leverage
                    or not math.isfinite(entry_ratio) or not 0 <= entry_ratio <= 1
                    or not math.isfinite(exit_ratio) or not 0 < exit_ratio <= 1
                    or (notional_value is not None and (not math.isfinite(notional_value) or notional_value <= 0))):
                raise ValueError("futures_strategy_configuration_out_of_range")
            from app.core.enums import Timeframe
            from app.engines.market_data import Candles
            from app.engines.technical import TechnicalAnalysisEngine
            from app.engines.backtest import BacktestEngine
            tf = timeframe if isinstance(timeframe, Timeframe) else Timeframe(str(timeframe))
            if strategy_key == "scalping_m5" and tf.value != "5m":
                return self._empty("scalping_m5_requires_5m_timeframe", self.initial_cash)
            if strategy_key == "swing_h1_h4" and tf.value not in {"1h", "4h"}:
                return self._empty("swing_h1_h4_requires_1h_or_4h_timeframe", self.initial_cash)
            if len(candles) < 60:
                return self._empty("at_least_60_candles_required", self.initial_cash)
            if not isinstance(pyramiding_enabled, bool) or isinstance(max_adds, bool) or not isinstance(max_adds, int):
                return self._empty("invalid_pyramiding_configuration", self.initial_cash)
            if any(isinstance(value, bool) for value in (add_fraction, add_trigger_pct)):
                return self._empty("boolean_pyramiding_configuration_not_allowed", self.initial_cash)
            add_fraction, add_trigger_pct = float(add_fraction), float(add_trigger_pct)
            if not math.isfinite(add_fraction) or not math.isfinite(add_trigger_pct) or not 0 < add_fraction <= 1 or not 0 < add_trigger_pct <= 0.1 or not 1 <= max_adds <= 5:
                return self._empty("pyramiding_configuration_out_of_range", self.initial_cash)
            total_cap = self.max_notional
            base_notional = min(notional_value if notional_value is not None else total_cap,
                                total_cap / (1.0 + max_adds * add_fraction)) if pyramiding_enabled else notional_value
            technical = TechnicalAnalysisEngine()
            signals: list[dict[str, Any]] = []
            active_side = None
            adds = 0
            last_add_reference = 0.0
            pending_entry = None
            active_stop = active_target = None
            for index in range(49, len(candles) - 1):
                risk_exit_this_candle = False
                if pending_entry is not None:
                    entry_fill = float(candles[index]["open"])
                    distance = pending_entry["stop_distance"]
                    target_distance = pending_entry["target_distance"]
                    active_side = pending_entry["side"]
                    if active_side == "long":
                        active_stop = entry_fill - distance
                        active_target = entry_fill + target_distance
                    else:
                        active_stop = entry_fill + distance
                        active_target = entry_fill - target_distance
                    pending_entry = None
                if active_side is not None:
                    low_price = float(candles[index]["low"])
                    high_price = float(candles[index]["high"])
                    stop_hit = active_stop is not None and (
                        low_price <= active_stop if active_side == "long" else high_price >= active_stop
                    )
                    target_hit = active_target is not None and (
                        high_price >= active_target if active_side == "long" else low_price <= active_target
                    )
                    if stop_hit or target_hit:
                        active_side = None
                        active_stop = active_target = None
                        adds = 0
                        last_add_reference = 0.0
                        risk_exit_this_candle = True
                analysis = technical.analyze(Candles(candles[:index + 1]), tf)
                action = BacktestEngine._strategy_action(strategy_key, analysis)
                close_price = float(candles[index]["close"])
                if action == "buy":
                    if active_side == "short":
                        signals.append({"index": index, "action": "close", "fill_ratio": exit_ratio})
                        if exit_ratio >= 1.0:
                            active_side, adds = None, 0
                            active_stop = active_target = None
                    elif active_side is None and not risk_exit_this_candle:
                        atr = float(analysis.indicators.get("atr14") or 0.0)
                        distance = atr * 1.5 if math.isfinite(atr) and atr > 0 else None
                        target_distance = atr * 2.5 if distance is not None else None
                        signal = {"index": index, "action": "long", "leverage": leverage_value,
                                  "fill_ratio": entry_ratio}
                        if base_notional is not None:
                            signal["notional"] = base_notional
                        if distance is not None:
                            signal["stop_distance"] = distance
                            signal["target_distance"] = target_distance
                            pending_entry = {"side": "long", "stop_distance": distance,
                                             "target_distance": target_distance}
                        signals.append(signal)
                        adds, last_add_reference = 0, close_price
                    elif active_side == "long" and pyramiding_enabled and adds < max_adds and close_price >= last_add_reference * (1.0 + add_trigger_pct):
                        signals.append({"index": index, "action": "long", "add_position": True,
                                         "leverage": leverage_value, "notional": float(base_notional or total_cap) * add_fraction})
                        adds += 1
                        last_add_reference = close_price
                elif action == "sell":
                    if active_side == "long":
                        signals.append({"index": index, "action": "close", "fill_ratio": exit_ratio})
                        if exit_ratio >= 1.0:
                            active_side, adds = None, 0
                            active_stop = active_target = None
                    elif active_side is None and not risk_exit_this_candle:
                        atr = float(analysis.indicators.get("atr14") or 0.0)
                        distance = atr * 1.5 if math.isfinite(atr) and atr > 0 else None
                        target_distance = atr * 2.5 if distance is not None else None
                        signal = {"index": index, "action": "short", "leverage": leverage_value,
                                  "fill_ratio": entry_ratio}
                        if base_notional is not None:
                            signal["notional"] = base_notional
                        if distance is not None:
                            signal["stop_distance"] = distance
                            signal["target_distance"] = target_distance
                            pending_entry = {"side": "short", "stop_distance": distance,
                                             "target_distance": target_distance}
                        signals.append(signal)
                        adds, last_add_reference = 0, close_price
                    elif active_side == "short" and pyramiding_enabled and adds < max_adds and close_price <= last_add_reference * (1.0 - add_trigger_pct):
                        signals.append({"index": index, "action": "short", "add_position": True,
                                         "leverage": leverage_value, "notional": float(base_notional or total_cap) * add_fraction})
                        adds += 1
                        last_add_reference = close_price
            result = self.run(candles, signals, funding_events=funding_events,
                              pyramiding_enabled=pyramiding_enabled, max_adds=max_adds)
            result.update({
                "strategy_key": strategy_key,
                "timeframe": tf.value,
                "generated_signals": len(signals),
                "signal_generation": "technical_analysis_on_causal_history_prefix",
                "pyramiding_enabled": pyramiding_enabled,
                "max_adds": max_adds if pyramiding_enabled else 0,
                "add_trigger_pct": add_trigger_pct if pyramiding_enabled else 0.0,
                "no_lookahead": True,
                "signal_causality": "verified_causal_history_prefix",
                "protective_exit_model": "intrabar_ohlc_stop_first_when_both_levels_hit",
            })
            return result
        except (TypeError, ValueError, KeyError) as exc:
            return self._empty("futures_strategy_backtest_failed:" + str(exc)[:160], self.initial_cash)

    def run(
        self,
        candles: list[dict],
        signals: list[dict],
        funding_events: Sequence[dict] = (),
        *,
        pyramiding_enabled: bool = False,
        max_adds: int = 2,
    ) -> dict[str, Any]:
        if not isinstance(pyramiding_enabled, bool) or isinstance(max_adds, bool) or not isinstance(max_adds, int) or not 1 <= max_adds <= 5:
            return self._empty("invalid_pyramiding_configuration", self.initial_cash)
        if not isinstance(candles, list) or not isinstance(signals, list):
            return self._empty("candles_and_signals_must_be_lists", self.initial_cash)
        if len(candles) < 2:
            return self._empty("at_least_two_candles_required", self.initial_cash)

        normalized = []
        timestamps = []
        for index, candle in enumerate(candles):
            if not isinstance(candle, dict):
                return self._empty(f"invalid_candle_at_index_{index}", self.initial_cash)
            if any(isinstance(candle.get(key), bool) for key in ("open", "high", "low", "close")):
                return self._empty(f"invalid_ohlc_at_index_{index}", self.initial_cash)
            try:
                o, h, low, close = (float(candle[key]) for key in ("open", "high", "low", "close"))
                stamp_raw = candle.get("timestamp", candle.get("close_time"))
                if isinstance(stamp_raw, bool) or stamp_raw is None:
                    raise ValueError("timestamp_required")
                stamp = float(stamp_raw)
            except (KeyError, TypeError, ValueError, OverflowError):
                return self._empty(f"invalid_candle_at_index_{index}", self.initial_cash)
            if (not all(math.isfinite(value) for value in (o, h, low, close, stamp))
                    or min(o, h, low, close) <= 0 or not stamp.is_integer()
                    or h < max(o, close, low) or low > min(o, close, h)):
                return self._empty(f"invalid_candle_at_index_{index}", self.initial_cash)
            normalized.append({"open": o, "high": h, "low": low, "close": close})
            timestamps.append(int(stamp))
        if any(timestamps[i] <= timestamps[i - 1] for i in range(1, len(timestamps))):
            return self._empty("candle_timestamps_must_increase", self.initial_cash)

        funding = []
        if not isinstance(funding_events, Sequence) or isinstance(funding_events, (str, bytes)):
            return self._empty("funding_events_must_be_a_sequence", self.initial_cash)
        for event in funding_events:
            if not isinstance(event, dict) or any(isinstance(event.get(k), bool) for k in ("timestamp", "rate")):
                return self._empty("invalid_funding_event", self.initial_cash)
            try:
                stamp = float(event["timestamp"])
                rate = float(event["rate"])
            except (KeyError, TypeError, ValueError, OverflowError):
                return self._empty("invalid_funding_event", self.initial_cash)
            if (not math.isfinite(stamp) or not stamp.is_integer()
                    or not math.isfinite(rate) or abs(rate) > 0.05):
                return self._empty("invalid_funding_event", self.initial_cash)
            funding.append({"timestamp": int(stamp), "rate": rate})
        funding.sort(key=lambda item: item["timestamp"])
        if any(funding[i]["timestamp"] <= funding[i - 1]["timestamp"] for i in range(1, len(funding))):
            return self._empty("funding_timestamps_must_be_unique", self.initial_cash)

        by_index: dict[int, dict[str, Any]] = {}
        for signal in signals:
            if not isinstance(signal, dict):
                return self._empty("signals_must_be_objects", self.initial_cash)
            action = str(signal.get("action", "")).lower()
            if action not in {"long", "short", "close", "reduce"}:
                continue
            raw_index = signal.get("index", signal.get("candle_index"))
            if isinstance(raw_index, bool) or raw_index is None:
                return self._empty("signal_index_required", self.initial_cash)
            try:
                numeric_index = float(raw_index)
                if not math.isfinite(numeric_index) or not numeric_index.is_integer():
                    raise ValueError("invalid_signal_index")
                index = int(numeric_index)
            except (TypeError, ValueError, OverflowError):
                return self._empty("invalid_signal_index", self.initial_cash)
            if index < 0 or index >= len(normalized) - 1:
                continue
            if index in by_index:
                return self._empty("multiple_futures_actions_same_candle", self.initial_cash)
            normalized_signal = signal | {"action": action, "index": index}
            for key in ("stop_distance", "target_distance"):
                if key in normalized_signal:
                    value = normalized_signal[key]
                    if isinstance(value, bool):
                        return self._empty("boolean_protective_exit_distance_not_allowed", self.initial_cash)
                    try:
                        value = float(value)
                    except (TypeError, ValueError, OverflowError):
                        return self._empty("invalid_protective_exit_distance", self.initial_cash)
                    if not math.isfinite(value) or value <= 0:
                        return self._empty("protective_exit_distance_out_of_range", self.initial_cash)
                    normalized_signal[key] = value
            by_index[index] = normalized_signal

        cash = self.initial_cash
        position: dict[str, Any] | None = None
        funding_cursor = 0
        fees_total = 0.0
        slippage_total = 0.0
        funding_net_total = 0.0
        liquidations = 0
        partial_fills = 0
        trades: list[dict[str, Any]] = []
        equity_curve = []

        def unrealized(mark: float) -> float:
            if position is None:
                return 0.0
            direction = 1.0 if position["side"] == "long" else -1.0
            return direction * (mark - position["entry_price"]) * position["quantity"]

        def close_quantity(quantity_to_close: float, raw_price: float, index: int, reason: str,
                           liquidation: bool = False) -> None:
            nonlocal cash, position, fees_total, slippage_total, liquidations
            if position is None or quantity_to_close <= 0:
                return
            quantity_to_close = min(quantity_to_close, position["quantity"])
            direction = 1.0 if position["side"] == "long" else -1.0
            fill = raw_price * (1.0 - self.slippage_rate if direction > 0 else 1.0 + self.slippage_rate)
            exit_notional = quantity_to_close * fill
            exit_fee_rate = self.liquidation_fee_rate if liquidation else self.fee_rate
            exit_fee = exit_notional * exit_fee_rate
            gross = direction * (fill - position["entry_price"]) * quantity_to_close
            fraction = quantity_to_close / position["quantity"]
            entry_fee_alloc = position["entry_fee_remaining"] * fraction
            funding_alloc = position["funding_net"] * fraction
            net = gross - entry_fee_alloc - exit_fee + funding_alloc
            cash += gross - exit_fee
            fees_total += exit_fee
            slippage_total += quantity_to_close * abs(raw_price - fill)
            trades.append({
                "side": position["side"], "entry_index": position["entry_index"],
                "exit_index": index, "entry_price": position["entry_price"],
                "exit_price": fill, "quantity": quantity_to_close,
                "leverage": position["leverage"], "gross_pnl": round(gross, 8),
                "entry_fee_allocated": round(entry_fee_alloc, 8),
                "exit_fee": round(exit_fee, 8), "funding_net_allocated": round(funding_alloc, 8),
                "net_pnl": round(net, 8), "exit_reason": reason,
                "liquidated": liquidation,
                "add_count": position.get("add_count", 0),
                "add_entries": list(position.get("add_entries", [])),
            })
            position["quantity"] -= quantity_to_close
            position["entry_fee_remaining"] *= (1.0 - fraction)
            position["funding_net"] *= (1.0 - fraction)
            position["initial_margin"] *= (1.0 - fraction)
            position["entry_notional"] *= (1.0 - fraction)
            if position["quantity"] <= 1e-12:
                position = None
            if liquidation:
                liquidations += 1

        def apply_funding_until(target_timestamp: int, mark_price: float) -> None:
            nonlocal cash, funding_cursor, funding_net_total
            while funding_cursor < len(funding) and funding[funding_cursor]["timestamp"] <= target_timestamp:
                event = funding[funding_cursor]
                if position is not None and event["timestamp"] > position["entry_timestamp"]:
                    direction = 1.0 if position["side"] == "long" else -1.0
                    payment = -direction * position["quantity"] * mark_price * event["rate"]
                    cash += payment
                    position["funding_net"] += payment
                    funding_net_total += payment
                funding_cursor += 1

        for index, candle in enumerate(normalized):
            mark = candle["close"]
            apply_funding_until(timestamps[index], mark)
            if position is not None:
                direction = 1.0 if position["side"] == "long" else -1.0
                maintenance = position["quantity"] * mark * self.maintenance_margin_rate
                margin_equity = (
                    position["initial_margin"] - position["entry_fee_remaining"]
                    + position["funding_net"] + unrealized(mark)
                )
                # Solve equity == maintenance margin directly. The prior
                # close-mark-dependent threshold shifted the intrabar trigger
                # as the candle close changed, rather than using a fixed level.
                margin_after_costs = (
                    position["initial_margin"] - position["entry_fee_remaining"]
                    + position["funding_net"]
                )
                if direction > 0:
                    liq_price = (
                        position["entry_price"] - margin_after_costs / position["quantity"]
                    ) / (1.0 - self.maintenance_margin_rate)
                else:
                    liq_price = (
                        position["entry_price"] + margin_after_costs / position["quantity"]
                    ) / (1.0 + self.maintenance_margin_rate)
                hit_liquidation = (
                    margin_equity <= maintenance
                    or (direction > 0 and candle["low"] <= liq_price)
                    or (direction < 0 and candle["high"] >= liq_price)
                )
                if hit_liquidation:
                    if direction > 0:
                        raw_liq_price = (
                            candle["open"] if candle["open"] <= liq_price
                            else liq_price if math.isfinite(liq_price) and liq_price > 0
                            else candle["low"]
                        )
                    else:
                        raw_liq_price = (
                            candle["open"] if candle["open"] >= liq_price
                            else liq_price if math.isfinite(liq_price) and liq_price > 0
                            else candle["high"]
                        )
                    close_quantity(position["quantity"], raw_liq_price, index, "estimated_liquidation", True)

            # Protective exits are evaluated on candle OHLC after liquidation.
            # When stop and target are both touched, the stop is assumed first.
            if position is not None:
                direction = 1.0 if position["side"] == "long" else -1.0
                stop = position.get("stop_loss")
                target = position.get("take_profit")
                stop_hit = (stop is not None and
                            (candle["low"] <= stop if direction > 0 else candle["high"] >= stop))
                target_hit = (target is not None and
                              (candle["high"] >= target if direction > 0 else candle["low"] <= target))
                if stop_hit:
                    raw_stop = (
                        candle["open"] if direction > 0 and candle["open"] <= stop
                        else candle["open"] if direction < 0 and candle["open"] >= stop
                        else stop
                    )
                    close_quantity(position["quantity"], raw_stop, index, "stop_loss")
                elif target_hit:
                    close_quantity(position["quantity"], target, index, "take_profit")

            equity_curve.append(cash + unrealized(mark))
            if index >= len(normalized) - 1:
                continue
            signal = by_index.get(index)
            if not signal:
                continue
            action = signal["action"]
            fill_open = normalized[index + 1]["open"]
            apply_funding_until(timestamps[index + 1], fill_open)
            if action in {"long", "short"}:
                adding = position is not None
                if adding and (
                    not pyramiding_enabled or signal.get("add_position") is not True
                    or position["side"] != action or position.get("add_count", 0) >= max_adds
                ):
                    continue
                if not adding and signal.get("add_position") is True:
                    continue
                raw_leverage = signal.get("leverage", 1.0)
                raw_notional = signal.get("notional")
                raw_fill_ratio = signal.get("fill_ratio", 1.0)
                if any(isinstance(value, bool) for value in (raw_leverage, raw_notional, raw_fill_ratio) if value is not None):
                    return self._empty("boolean_signal_configuration_not_allowed", self.initial_cash)
                try:
                    leverage = float(raw_leverage)
                    fill_ratio = float(raw_fill_ratio)
                    requested = float(raw_notional) if raw_notional is not None else (
                        cash * self.allocation_fraction * leverage
                    )
                except (TypeError, ValueError, OverflowError):
                    return self._empty("invalid_futures_signal_configuration", self.initial_cash)
                if (not all(math.isfinite(value) for value in (leverage, fill_ratio, requested))
                        or leverage < 1 or leverage > self.max_leverage
                        or not 0 <= fill_ratio <= 1 or requested <= 0):
                    return self._empty("futures_signal_configuration_out_of_range", self.initial_cash)
                remaining_cap = max(0.0, self.max_notional - position["entry_notional"]) if adding else self.max_notional
                remaining_cash_cap = max(0.0, cash * leverage - (position["entry_notional"] if adding else 0.0))
                notional = min(requested, remaining_cap, remaining_cash_cap)
                if notional <= 0 or notional / leverage > cash:
                    continue
                if fill_ratio == 0:
                    continue
                if fill_ratio < 1:
                    partial_fills += 1
                notional *= fill_ratio
                direction = 1.0 if action == "long" else -1.0
                fill = fill_open * (1.0 + self.slippage_rate if direction > 0 else 1.0 - self.slippage_rate)
                quantity = notional / fill
                entry_fee = notional * self.fee_rate
                if cash < entry_fee:
                    continue
                cash -= entry_fee
                fees_total += entry_fee
                slippage_total += quantity * abs(fill_open - fill)
                if adding:
                    previous_quantity = position["quantity"]
                    position["entry_price"] = (position["entry_price"] * previous_quantity + fill * quantity) / (previous_quantity + quantity)
                    position["quantity"] += quantity
                    position["entry_notional"] += notional
                    position["initial_margin"] += notional / leverage
                    position["entry_fee_remaining"] += entry_fee
                    position["add_count"] = position.get("add_count", 0) + 1
                    position.setdefault("add_entries", []).append({
                        "entry_index": index + 1, "entry_price": fill,
                        "quantity": quantity, "notional": notional,
                    })
                else:
                    stop_distance = signal.get("stop_distance")
                    target_distance = signal.get("target_distance")
                    position = {
                        "side": action, "entry_price": fill, "quantity": quantity,
                        "leverage": leverage, "entry_notional": notional,
                        "initial_margin": notional / leverage,
                        "entry_fee_remaining": entry_fee, "funding_net": 0.0,
                        "entry_index": index + 1, "entry_timestamp": timestamps[index + 1],
                        "stop_loss": (fill - stop_distance if action == "long" else fill + stop_distance)
                            if stop_distance is not None else None,
                        "take_profit": (fill + target_distance if action == "long" else fill - target_distance)
                            if target_distance is not None else None,
                        "add_count": 0, "add_entries": [],
                    }
            elif action in {"close", "reduce"} and position is not None:
                raw_fill_ratio = signal.get("fill_ratio", 1.0 if action == "close" else 0.5)
                if isinstance(raw_fill_ratio, bool):
                    return self._empty("boolean_fill_ratio_not_allowed", self.initial_cash)
                try:
                    fill_ratio = float(raw_fill_ratio)
                except (TypeError, ValueError, OverflowError):
                    return self._empty("invalid_fill_ratio", self.initial_cash)
                if not math.isfinite(fill_ratio) or not 0 < fill_ratio <= 1:
                    return self._empty("fill_ratio_out_of_range", self.initial_cash)
                if fill_ratio < 1:
                    partial_fills += 1
                quantity_to_close = position["quantity"] * fill_ratio
                direction = 1.0 if position["side"] == "long" else -1.0
                raw_exit = fill_open
                close_quantity(quantity_to_close, raw_exit, index + 1, "signal_close" if fill_ratio == 1 else "partial_reduce")

        if position is not None:
            raw_exit = normalized[-1]["close"]
            close_quantity(position["quantity"], raw_exit, len(normalized) - 1, "end_of_data")
            equity_curve[-1] = cash

        pnl = [trade["net_pnl"] for trade in trades]
        wins = [value for value in pnl if value > 0]
        final_equity = cash
        peak = self.initial_cash
        max_drawdown = 0.0
        for value in equity_curve:
            peak = max(peak, value)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - value) / peak)
        return {
            "ok": True,
            "total_trades": len(trades),
            "win_rate": round(len(wins) / len(trades), 4) if trades else 0.0,
            "total_return": round((final_equity - self.initial_cash) / self.initial_cash, 6),
            "final_equity": round(final_equity, 8),
            "max_drawdown": round(max_drawdown, 6),
            "fees_paid": round(fees_total, 8),
            "slippage_cost": round(slippage_total, 8),
            "funding_net": round(funding_net_total, 8),
            "liquidations": liquidations,
            "partial_fills": partial_fills,
            "trades": trades,
            "equity_curve": [round(value, 8) for value in equity_curve],
            "execution_model": "next_candle_open_with_fees_slippage_funding_partial_fills",
            "margin_model": "single_net_position_isolated_margin_with_estimated_liquidation",
            "pyramiding_enabled": pyramiding_enabled,
            "max_adds": max_adds if pyramiding_enabled else 0,
            "max_notional": self.max_notional,
            "no_lookahead": False,
            "signal_causality": "not_verified_for_external_signals",
            "execution": "none",
            "limitations": [
                "single isolated-margin position; no cross-margin portfolio netting",
                "liquidation is an estimate using one maintenance-margin rate, not Binance risk tiers",
                "funding uses supplied timestamps and candle-close mark approximation",
                "no order-book queue, exchange filters, latency, or market-impact model",
                "partial fills are user-supplied fill ratios, not a stochastic order-book simulation",
            ],
        }
