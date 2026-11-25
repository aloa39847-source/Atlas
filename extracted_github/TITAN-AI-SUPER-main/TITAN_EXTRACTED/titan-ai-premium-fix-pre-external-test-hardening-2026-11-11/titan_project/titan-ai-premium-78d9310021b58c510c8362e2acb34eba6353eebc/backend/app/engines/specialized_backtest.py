"""Specialized historical replay for strategies that cannot be validated from OHLCV candles alone.

All methods are offline simulations. They never submit exchange orders.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping, Sequence


class SpecializedBacktestEngine:
    """Replay order-flow events and synchronized triangular-arbitrage snapshots."""

    def __init__(
        self,
        *,
        initial_cash: float = 10_000.0,
        fee_rate: float = 0.001,
        slippage_rate: float = 0.0005,
        max_notional: float = 80.0,
    ) -> None:
        raw = (initial_cash, fee_rate, slippage_rate, max_notional)
        if any(isinstance(value, bool) for value in raw):
            raise ValueError("boolean_specialized_backtest_configuration_not_allowed")
        try:
            values = tuple(float(value) for value in raw)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("invalid_specialized_backtest_configuration")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("non_finite_specialized_backtest_configuration")
        self.initial_cash, self.fee_rate, self.slippage_rate, self.max_notional = values
        if (self.initial_cash <= 0 or not 0 <= self.fee_rate < 1
                or not 0 <= self.slippage_rate < 1 or self.max_notional <= 0):
            raise ValueError("specialized_backtest_configuration_out_of_range")

    def _empty(self, reason: str) -> dict[str, Any]:
        return {
            "ok": False, "error": reason, "total_trades": 0,
            "initial_cash": self.initial_cash, "final_equity": self.initial_cash,
            "total_return": 0.0, "fees_paid": 0.0, "slippage_cost": 0.0,
            "trades": [], "equity_curve": [self.initial_cash],
            "execution": "simulated_only", "no_live_orders": True,
            "no_lookahead": False,
        }

    @staticmethod
    def _number(record: dict, key: str) -> float:
        value = record.get(key)
        if isinstance(value, bool):
            raise ValueError("boolean_market_data_value")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non_finite_market_data_value")
        return number

    def run_orderflow_replay(
        self,
        events: Sequence[dict],
        signals: Sequence[dict],
    ) -> dict[str, Any]:
        """Replay timestamped public book/flow events; signals fill on the next event.

        Event fields: timestamp, symbol, best_bid, best_ask, bid_depth_quote,
        ask_depth_quote, depth_imbalance and taker_flow_imbalance.
        Signals are indexed buy/sell decisions from the strategy under test.
        """
        if (not isinstance(events, Sequence) or isinstance(events, (str, bytes))
                or not isinstance(signals, Sequence) or isinstance(signals, (str, bytes))):
            return self._empty("events_and_signals_must_be_sequences")
        if len(events) < 2:
            return self._empty("at_least_two_orderflow_events_required")
        normalized = []
        symbol = None
        for index, event in enumerate(events):
            if not isinstance(event, dict):
                return self._empty(f"invalid_orderflow_event_{index}")
            try:
                stamp = self._number(event, "timestamp")
                bid = self._number(event, "best_bid")
                ask = self._number(event, "best_ask")
                bid_depth = self._number(event, "bid_depth_quote")
                ask_depth = self._number(event, "ask_depth_quote")
                depth_imbalance = self._number(event, "depth_imbalance")
                flow_imbalance = self._number(event, "taker_flow_imbalance")
                event_symbol = str(event.get("symbol") or "").upper()
                if (not stamp.is_integer() or bid <= 0 or ask <= bid
                        or bid_depth < 0 or ask_depth < 0
                        or not -1 <= depth_imbalance <= 1
                        or not -1 <= flow_imbalance <= 1 or not event_symbol):
                    raise ValueError("invalid_orderflow_event_values")
                if symbol is None:
                    symbol = event_symbol
                if event_symbol != symbol:
                    raise ValueError("orderflow_replay_symbol_mismatch")
                normalized.append({
                    "timestamp": int(stamp), "bid": bid, "ask": ask,
                    "bid_depth": bid_depth, "ask_depth": ask_depth,
                })
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                return self._empty(f"invalid_orderflow_event_{index}:{str(exc)[:100]}")
        if any(normalized[i]["timestamp"] <= normalized[i - 1]["timestamp"] for i in range(1, len(normalized))):
            return self._empty("orderflow_timestamps_must_increase")

        by_index = {}
        for signal in signals:
            if not isinstance(signal, dict):
                return self._empty("orderflow_signals_must_be_objects")
            action = str(signal.get("action") or "").lower()
            if action not in {"buy", "sell"}:
                continue
            raw_index = signal.get("index")
            if isinstance(raw_index, bool) or raw_index is None:
                return self._empty("orderflow_signal_index_required")
            try:
                numeric = float(raw_index)
                if not math.isfinite(numeric) or not numeric.is_integer():
                    raise ValueError()
                idx = int(numeric)
            except (TypeError, ValueError, OverflowError):
                return self._empty("invalid_orderflow_signal_index")
            if idx < 0 or idx >= len(normalized) - 1:
                continue
            if idx in by_index:
                return self._empty("multiple_orderflow_actions_same_event")
            by_index[idx] = action

        cash = self.initial_cash
        position = None
        fees = 0.0
        slippage = 0.0
        trades = []
        equity = [cash]
        for index in range(len(normalized) - 1):
            event = normalized[index + 1]
            action = by_index.get(index)
            if action == "buy" and position is None:
                raw_fill = event["ask"]
                fill = raw_fill * (1 + self.slippage_rate)
                budget = min(cash, self.max_notional, event["ask_depth"])
                if budget > 0:
                    quantity = budget / (fill * (1 + self.fee_rate))
                    notional = quantity * fill
                    fee = notional * self.fee_rate
                    cash -= notional + fee
                    fees += fee
                    slippage += quantity * abs(fill - raw_fill)
                    position = {"quantity": quantity, "entry": fill, "entry_fee": fee,
                                "entry_index": index + 1, "entry_timestamp": event["timestamp"]}
            elif action == "sell" and position is not None:
                raw_fill = event["bid"]
                quantity = min(position["quantity"], event["bid_depth"] / raw_fill if raw_fill else 0)
                if quantity > 0:
                    fill = raw_fill * (1 - self.slippage_rate)
                    proceeds = quantity * fill
                    exit_fee = proceeds * self.fee_rate
                    fraction = quantity / position["quantity"]
                    entry_fee = position["entry_fee"] * fraction
                    gross = quantity * (fill - position["entry"])
                    net = gross - entry_fee - exit_fee
                    cash += proceeds - exit_fee
                    fees += exit_fee
                    slippage += quantity * abs(raw_fill - fill)
                    trades.append({
                        "symbol": symbol, "entry_index": position["entry_index"],
                        "exit_index": index + 1, "entry_price": position["entry"],
                        "exit_price": fill, "quantity": quantity,
                        "gross_pnl": round(gross, 8), "fees": round(entry_fee + exit_fee, 8),
                        "net_pnl": round(net, 8), "exit_reason": "orderflow_sell_signal",
                    })
                    position["quantity"] -= quantity
                    position["entry_fee"] -= entry_fee
                    if position["quantity"] <= 1e-12:
                        position = None
            mark = event["bid"]
            equity.append(cash + (position["quantity"] * mark if position else 0.0))

        if position is not None:
            last = normalized[-1]
            raw_fill = last["bid"]
            quantity = min(position["quantity"], last["bid_depth"] / raw_fill if raw_fill else 0)
            if quantity > 0:
                fill = raw_fill * (1 - self.slippage_rate)
                proceeds = quantity * fill
                exit_fee = proceeds * self.fee_rate
                gross = quantity * (fill - position["entry"])
                fraction = quantity / position["quantity"]
                entry_fee_alloc = position["entry_fee"] * fraction
                net = gross - entry_fee_alloc - exit_fee
                cash += proceeds - exit_fee
                fees += exit_fee
                slippage += quantity * abs(raw_fill - fill)
                trades.append({
                    "symbol": symbol, "entry_index": position["entry_index"],
                    "exit_index": len(normalized) - 1, "entry_price": position["entry"],
                    "exit_price": fill, "quantity": quantity,
                    "gross_pnl": round(gross, 8),
                    "fees": round(entry_fee_alloc + exit_fee, 8),
                    "net_pnl": round(net, 8), "exit_reason": "end_of_data",
                })
                position["quantity"] -= quantity
                position["entry_fee"] -= entry_fee_alloc
            equity[-1] = cash + (position["quantity"] * last["bid"] if position else 0.0)

        final_equity = equity[-1]
        pnl = [trade["net_pnl"] for trade in trades]
        wins = sum(1 for value in pnl if value > 0)
        return {
            "ok": True, "strategy_key": "liquidity_orderflow", "symbol": symbol,
            "total_trades": len(trades), "win_rate": round(wins / len(trades), 4) if trades else 0.0,
            "total_return": round((final_equity - self.initial_cash) / self.initial_cash, 6),
            "final_equity": round(final_equity, 8), "fees_paid": round(fees, 8),
            "slippage_cost": round(slippage, 8), "trades": trades,
            "equity_curve": [round(value, 8) for value in equity],
            "execution_model": "next_event_top_of_book_depth_capped",
            "position_mode": "spot_long_only_single_position",
            "signal_causality": "external_signal_causality_not_verified",
            "no_lookahead": False, "execution": "simulated_only", "no_live_orders": True,
            "limitations": [
                "signals are supplied by the caller and must be generated causally",
                "top-of-book depth is a conservative quote-notional cap, not queue-position simulation",
                "single Spot long position; no partial queue fills, latency or exchange filters",
            ],
        }


    def run_aggregate_trade_flow_replay(
        self,
        trades: Sequence[dict],
        *,
        rolling_window: int = 30,
        entry_imbalance: float = 0.15,
        exit_imbalance: float = 0.0,
    ) -> dict[str, Any]:
        """Generate causal taker-flow signals from historical Binance aggregate trades.

        A signal is computed only from the trailing window ending at index i and
        filled against the next observed aggregate-trade price at i+1.
        """
        if not isinstance(trades, Sequence) or isinstance(trades, (str, bytes)):
            return self._empty("aggregate_trades_must_be_a_sequence")
        if isinstance(rolling_window, bool) or not isinstance(rolling_window, int) or not 5 <= rolling_window <= 500:
            return self._empty("rolling_window_must_be_between_5_and_500")
        if any(isinstance(value, bool) for value in (entry_imbalance, exit_imbalance)):
            return self._empty("boolean_orderflow_threshold_not_allowed")
        try:
            entry_threshold = float(entry_imbalance)
            exit_threshold = float(exit_imbalance)
        except (TypeError, ValueError, OverflowError):
            return self._empty("invalid_orderflow_threshold")
        if (not math.isfinite(entry_threshold) or not math.isfinite(exit_threshold)
                or not 0.05 <= entry_threshold <= 0.95
                or not -0.5 <= exit_threshold <= 0.5
                or exit_threshold >= entry_threshold):
            return self._empty("orderflow_threshold_out_of_range")
        if len(trades) < rolling_window + 2 or len(trades) > 50000:
            return self._empty("aggregate_trade_history_length_out_of_range")

        normalized = []
        symbol = None
        previous_time = -1
        previous_id = -1
        for index, trade in enumerate(trades):
            if not isinstance(trade, dict) or not isinstance(trade.get("m"), bool):
                return self._empty(f"invalid_aggregate_trade_{index}")
            try:
                if any(isinstance(trade.get(key), bool) for key in ("a", "T", "p", "q")):
                    raise ValueError("boolean_aggregate_trade_field")
                trade_id = int(trade["a"])
                stamp = int(trade["T"])
                price = float(trade["p"])
                quantity = float(trade["q"])
                event_symbol = str(trade.get("s") or trade.get("symbol") or "").upper()
                if (trade_id < 0 or stamp <= 0 or price <= 0 or quantity <= 0
                        or not all(math.isfinite(value) for value in (price, quantity))
                        or stamp < previous_time or trade_id <= previous_id or not event_symbol):
                    raise ValueError("invalid_aggregate_trade_values_or_order")
                if symbol is None:
                    symbol = event_symbol
                if event_symbol != symbol:
                    raise ValueError("aggregate_trade_symbol_mismatch")
                quote = price * quantity
                if not math.isfinite(quote) or quote <= 0:
                    raise ValueError("invalid_aggregate_trade_quote")
                normalized.append({
                    "id": trade_id, "timestamp": stamp, "price": price,
                    "quote": quote, "buyer_is_maker": trade["m"],
                    "buy_quote": 0.0 if trade["m"] else quote,
                    "sell_quote": quote if trade["m"] else 0.0,
                })
                previous_time, previous_id = stamp, trade_id
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                return self._empty(f"invalid_aggregate_trade_{index}:{str(exc)[:100]}")

        cash = self.initial_cash
        position = None
        fees = 0.0
        slippage = 0.0
        trades_out = []
        equity = [cash]
        buy_window = []
        sell_window = []
        buy_sum = sell_sum = 0.0
        pending_action = None
        for index, item in enumerate(normalized):
            # Execute only a signal produced by the preceding event.
            if pending_action == "buy" and position is None:
                raw_fill = item["price"]
                fill = raw_fill * (1.0 + self.slippage_rate)
                budget = min(cash, self.max_notional)
                quantity = budget / (fill * (1.0 + self.fee_rate))
                notional = quantity * fill
                fee = notional * self.fee_rate
                if quantity > 0 and notional + fee <= cash + 1e-9:
                    cash -= notional + fee
                    fees += fee
                    slippage += quantity * abs(fill - raw_fill)
                    position = {
                        "quantity": quantity, "entry": fill, "entry_fee": fee,
                        "entry_index": index, "entry_timestamp": item["timestamp"],
                    }
            elif pending_action == "sell" and position is not None:
                raw_fill = item["price"]
                fill = raw_fill * (1.0 - self.slippage_rate)
                quantity = position["quantity"]
                proceeds = quantity * fill
                exit_fee = proceeds * self.fee_rate
                gross = quantity * (fill - position["entry"])
                net = gross - position["entry_fee"] - exit_fee
                cash += proceeds - exit_fee
                fees += exit_fee
                slippage += quantity * abs(raw_fill - fill)
                trades_out.append({
                    "symbol": symbol, "entry_index": position["entry_index"],
                    "exit_index": index, "entry_timestamp": position["entry_timestamp"],
                    "exit_timestamp": item["timestamp"],
                    "entry_price": position["entry"], "exit_price": fill,
                    "quantity": quantity, "gross_pnl": round(gross, 8),
                    "fees": round(position["entry_fee"] + exit_fee, 8),
                    "net_pnl": round(net, 8), "exit_reason": "rolling_taker_flow_reversal",
                })
                position = None
            pending_action = None

            buy_window.append(item["buy_quote"])
            sell_window.append(item["sell_quote"])
            buy_sum += item["buy_quote"]
            sell_sum += item["sell_quote"]
            if len(buy_window) > rolling_window:
                buy_sum -= buy_window.pop(0)
                sell_sum -= sell_window.pop(0)

            if index >= rolling_window - 1 and index < len(normalized) - 1:
                total = buy_sum + sell_sum
                imbalance = (buy_sum - sell_sum) / total if total > 0 else 0.0
                if position is None and imbalance >= entry_threshold:
                    pending_action = "buy"
                elif position is not None and imbalance <= exit_threshold:
                    pending_action = "sell"

            equity.append(cash + (position["quantity"] * item["price"] if position else 0.0))

        if position is not None:
            last = normalized[-1]
            raw_fill = last["price"]
            fill = raw_fill * (1.0 - self.slippage_rate)
            quantity = position["quantity"]
            proceeds = quantity * fill
            exit_fee = proceeds * self.fee_rate
            gross = quantity * (fill - position["entry"])
            net = gross - position["entry_fee"] - exit_fee
            cash += proceeds - exit_fee
            fees += exit_fee
            slippage += quantity * abs(raw_fill - fill)
            trades_out.append({
                "symbol": symbol, "entry_index": position["entry_index"],
                "exit_index": len(normalized) - 1, "entry_timestamp": position["entry_timestamp"],
                "exit_timestamp": last["timestamp"], "entry_price": position["entry"],
                "exit_price": fill, "quantity": quantity,
                "gross_pnl": round(gross, 8),
                "fees": round(position["entry_fee"] + exit_fee, 8),
                "net_pnl": round(net, 8), "exit_reason": "end_of_data",
            })
            equity[-1] = cash

        final_equity = equity[-1]
        pnl = [trade["net_pnl"] for trade in trades_out]
        wins = sum(value > 0 for value in pnl)
        return {
            "ok": True, "strategy_key": "liquidity_orderflow",
            "symbol": symbol, "market_type": "spot",
            "total_trades": len(trades_out),
            "win_rate": round(wins / len(trades_out), 4) if trades_out else 0.0,
            "total_return": round((final_equity - self.initial_cash) / self.initial_cash, 8),
            "final_equity": round(final_equity, 8),
            "fees_paid": round(fees, 8), "slippage_cost": round(slippage, 8),
            "trades": trades_out, "equity_curve": [round(value, 8) for value in equity],
            "data_points": len(normalized), "rolling_window": rolling_window,
            "entry_imbalance": entry_threshold, "exit_imbalance": exit_threshold,
            "signal_generation": "causal_rolling_aggregate_taker_flow",
            "execution_model": "next_aggregate_trade_price_plus_configured_slippage",
            "position_mode": "spot_long_only_single_position",
            "no_lookahead": True, "execution": "simulated_only", "no_live_orders": True,
            "limitations": [
                "historical aggregate trades are genuine public trade prints, but historical order-book depth is not available in this replay",
                "fills use the next observed aggregate-trade price plus configured slippage; this is not a guaranteed executable quote",
                "fees are configured estimates and do not include account-specific fee tiers",
                "one Spot long position at a time; no queue position, latency or partial book-depth fills",
            ],
        }


    def run_futures_aggregate_trade_flow_replay(
        self,
        trades: Sequence[dict],
        *,
        funding_events: Sequence[dict] = (),
        rolling_window: int = 30,
        entry_imbalance: float = 0.15,
        exit_imbalance: float = 0.0,
        leverage: float = 2.0,
        max_leverage: float = 10.0,
        notional: float = 800.0,
    ) -> dict[str, Any]:
        """Replay causal USD-M taker-flow signals against next-trade fills.

        Aggregate trades are converted to one-price event bars solely to reuse
        the isolated-margin Futures simulator. This is not historical order-book
        depth replay and does not model queue position or partial depth fills.
        """
        if not isinstance(trades, Sequence) or isinstance(trades, (str, bytes)):
            return self._empty("futures_aggregate_trades_must_be_a_sequence")
        if isinstance(rolling_window, bool) or not isinstance(rolling_window, int) or not 5 <= rolling_window <= 1000:
            return self._empty("futures_orderflow_rolling_window_out_of_range")
        raw_config = (entry_imbalance, exit_imbalance, leverage, max_leverage, notional)
        if any(isinstance(value, bool) for value in raw_config):
            return self._empty("boolean_futures_orderflow_configuration_not_allowed")
        try:
            entry, exit_, leverage_value, leverage_cap, notional_value = map(float, raw_config)
        except (TypeError, ValueError, OverflowError):
            return self._empty("invalid_futures_orderflow_configuration")
        if (not all(math.isfinite(value) for value in (entry, exit_, leverage_value, leverage_cap, notional_value))
                or not 0.05 <= entry <= 0.95 or not -0.5 <= exit_ <= 0.5
                or abs(exit_) >= entry or leverage_value < 1 or leverage_cap < 1
                or leverage_value > leverage_cap or notional_value <= 0):
            return self._empty("futures_orderflow_configuration_out_of_range")
        if len(trades) < rolling_window + 2 or len(trades) > 50000:
            return self._empty("futures_aggregate_trade_history_length_out_of_range")

        normalized = []
        symbol = None
        previous_time = -1
        previous_id = -1
        for index, trade in enumerate(trades):
            if not isinstance(trade, dict) or not isinstance(trade.get("m"), bool):
                return self._empty(f"invalid_futures_aggregate_trade_{index}")
            try:
                if any(isinstance(trade.get(key), bool) for key in ("a", "T", "p", "q")):
                    raise ValueError("boolean_aggregate_trade_field")
                trade_id, stamp = int(trade["a"]), int(trade["T"])
                price, quantity = float(trade["p"]), float(trade["q"])
                event_symbol = str(trade.get("s") or trade.get("symbol") or "").upper()
                if (trade_id < 0 or stamp <= 0 or price <= 0 or quantity <= 0
                        or not math.isfinite(price) or not math.isfinite(quantity)
                        or stamp < previous_time or trade_id <= previous_id or not event_symbol):
                    raise ValueError("invalid_aggregate_trade_values_or_order")
                if symbol is None:
                    symbol = event_symbol
                if event_symbol != symbol:
                    raise ValueError("aggregate_trade_symbol_mismatch")
                normalized.append({
                    "a": trade_id, "T": stamp, "p": price, "q": quantity,
                    "m": trade["m"], "s": event_symbol,
                })
                previous_time, previous_id = stamp, trade_id
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                return self._empty(f"invalid_futures_aggregate_trade_{index}:{str(exc)[:100]}")

        from app.engines.futures_backtest import FuturesBacktestEngine
        candles = [{
            "timestamp": item["T"], "open": item["p"], "high": item["p"],
            "low": item["p"], "close": item["p"], "volume": float(item["q"]),
        } for item in normalized]
        buy_window: list[float] = []
        sell_window: list[float] = []
        buy_sum = sell_sum = 0.0
        active_side = None
        signals = []
        for index, item in enumerate(normalized[:-1]):
            quote = item["p"] * item["q"]
            buy = 0.0 if item["m"] else quote
            sell = quote if item["m"] else 0.0
            buy_window.append(buy)
            sell_window.append(sell)
            buy_sum += buy
            sell_sum += sell
            if len(buy_window) > rolling_window:
                buy_sum -= buy_window.pop(0)
                sell_sum -= sell_window.pop(0)
            if index < rolling_window - 1:
                continue
            total = buy_sum + sell_sum
            imbalance = (buy_sum - sell_sum) / total if total > 0 else 0.0
            if active_side is None:
                if imbalance >= entry:
                    signals.append({"index": index, "action": "long", "leverage": leverage_value,
                                    "notional": min(notional_value, 800.0)})
                    active_side = "long"
                elif imbalance <= -entry:
                    signals.append({"index": index, "action": "short", "leverage": leverage_value,
                                    "notional": min(notional_value, 800.0)})
                    active_side = "short"
            elif active_side == "long" and imbalance <= exit_:
                signals.append({"index": index, "action": "close"})
                active_side = None
            elif active_side == "short" and imbalance >= -exit_:
                signals.append({"index": index, "action": "close"})
                active_side = None

        engine = FuturesBacktestEngine(
            initial_cash=self.initial_cash,
            fee_rate=self.fee_rate,
            slippage_rate=self.slippage_rate,
            max_notional=min(notional_value, 800.0),
            max_leverage=leverage_cap,
        )
        result = engine.run(candles, signals, funding_events=funding_events)
        result.update({
            "strategy_key": "liquidity_orderflow",
            "market_type": "futures",
            "symbol": symbol,
            "generated_signals": len(signals),
            "data_points": len(normalized),
            "rolling_window": rolling_window,
            "entry_imbalance": entry,
            "exit_imbalance": exit_,
            "leverage": leverage_value,
            "max_notional": min(notional_value, 800.0),
            "signal_generation": "causal_rolling_aggregate_taker_flow",
            "execution_model": "next_aggregate_trade_event_price_plus_configured_slippage",
            "no_lookahead": True,
            "signal_causality": "verified_causal_rolling_trade_prefix",
            "execution": "simulated_only",
            "no_live_orders": True,
            "limitations": [
                "historical aggregate trades are genuine USD-M Futures trade prints, not historical order-book depth",
                "each event is represented as a one-price bar; fills occur at the next observed aggregate trade price plus modeled slippage",
                "funding is applied only when matching historical funding events are supplied",
                "queue position, latency, historical bid/ask depth and partial book-depth fills are not modeled",
                "liquidation is estimated by the isolated-margin simulator and is not exchange-exact",
            ],
        })
        return result

    def run_triangular_candle_replay(
        self,
        histories: Mapping[str, list[dict]],
        *,
        route: list[str],
        pair_symbols: list[str],
        min_profit_pct: float = 0.05,
    ) -> dict[str, Any]:
        """Replay a three-leg conversion using synchronized public OHLCV candles.

        Signals are evaluated from candle closes and each complete three-leg
        cycle is executed at the next candle opens with fee/slippage estimates.
        This is a candle-price approximation, not historical order-book replay.
        """
        if not isinstance(histories, Mapping) or len(histories) != 3:
            return self._empty("three_pair_candle_histories_required")
        if (not isinstance(route, list) or len(route) != 4
                or any(not isinstance(asset, str) or not asset.isascii() or not asset.isalnum() for asset in route)
                or route[0] != route[-1] or len(set(route[:3])) != 3):
            return self._empty("triangle_route_must_be_three_distinct_assets_and_closed")
        if (not isinstance(pair_symbols, list) or len(pair_symbols) != 3
                or any(not isinstance(symbol, str) or not symbol.isascii() or not symbol.isalnum()
                       for symbol in pair_symbols)
                or len(set(pair_symbols)) != 3):
            return self._empty("three_distinct_pair_symbols_required")
        if isinstance(min_profit_pct, bool):
            return self._empty("boolean_min_profit_threshold_not_allowed")
        try:
            threshold = float(min_profit_pct)
        except (TypeError, ValueError, OverflowError):
            return self._empty("invalid_min_profit_threshold")
        if not math.isfinite(threshold) or not 0 <= threshold <= 100:
            return self._empty("min_profit_threshold_out_of_range")

        normalized_histories = {}
        timestamp_sets = []
        for raw_symbol, candles in histories.items():
            symbol = str(raw_symbol).upper().replace("/", "")
            if symbol not in pair_symbols or symbol in normalized_histories:
                return self._empty("unexpected_or_duplicate_triangle_pair")
            if not isinstance(candles, list) or not 2 <= len(candles) <= 500:
                return self._empty(f"invalid_candle_history_length_for_{symbol}")
            rows = {}
            stamps = []
            for index, candle in enumerate(candles):
                if not isinstance(candle, dict):
                    return self._empty(f"invalid_candle_for_{symbol}_{index}")
                try:
                    stamp_raw = candle.get("timestamp", candle.get("close_time"))
                    if isinstance(stamp_raw, bool) or not isinstance(stamp_raw, (int, float)):
                        raise ValueError("timestamp_required")
                    stamp = float(stamp_raw)
                    if not stamp.is_integer():
                        raise ValueError("invalid_timestamp")
                    open_price = self._number(candle, "open")
                    close_price = self._number(candle, "close")
                    if open_price <= 0 or close_price <= 0:
                        raise ValueError("non_positive_price")
                    if stamp in rows:
                        raise ValueError("duplicate_timestamp")
                    rows[int(stamp)] = {
                        "open": open_price, "close": close_price,
                    }
                    stamps.append(int(stamp))
                except (KeyError, TypeError, ValueError, OverflowError) as exc:
                    return self._empty(f"invalid_candle_for_{symbol}_{index}:{str(exc)[:80]}")
            if any(stamps[i] <= stamps[i - 1] for i in range(1, len(stamps))):
                return self._empty(f"candle_timestamps_must_increase_for_{symbol}")
            normalized_histories[symbol] = rows
            timestamp_sets.append(stamps)
        if set(normalized_histories) != set(pair_symbols):
            return self._empty("missing_triangle_pair_history")

        for index, symbol in enumerate(pair_symbols):
            asset_in, asset_out = route[index], route[index + 1]
            if symbol not in {asset_in + asset_out, asset_out + asset_in}:
                return self._empty(f"pair_symbol_does_not_match_route_leg_{index}")
        common = set(timestamp_sets[0])
        for stamps in timestamp_sets[1:]:
            common.intersection_update(stamps)
        aligned = sorted(common)
        if len(aligned) < 2:
            return self._empty("triangle_histories_need_two_common_timestamps")

        def convert(amount: float, index: int, prices: dict[str, float],
                    fee: float, slip: float) -> float:
            current = amount
            asset_in, asset_out = route[index], route[index + 1]
            symbol = pair_symbols[index]
            price = prices[symbol]
            if symbol == asset_in + asset_out:
                # Symbol base=asset_in, quote=asset_out: sell base for quote.
                current = current * price * (1.0 - slip)
            elif symbol == asset_out + asset_in:
                # Symbol base=asset_out, quote=asset_in: buy base with quote.
                current = current / (price * (1.0 + slip))
            else:
                raise ValueError("triangle_pair_orientation_mismatch")
            return current * (1.0 - fee)

        cash = self.initial_cash
        cycles = []
        equity = [cash]
        fees_paid = 0.0
        slippage_cost = 0.0
        for index in range(len(aligned) - 1):
            signal_stamp = aligned[index]
            fill_stamp = aligned[index + 1]
            close_prices = {
                symbol: normalized_histories[symbol][signal_stamp]["close"]
                for symbol in pair_symbols
            }
            candidate = cash
            for leg in range(3):
                candidate = convert(candidate, leg, close_prices, self.fee_rate, self.slippage_rate)
            candidate_pct = (candidate / cash - 1.0) * 100.0 if cash > 0 else -100.0
            if candidate_pct >= threshold:
                open_prices = {
                    symbol: normalized_histories[symbol][fill_stamp]["open"]
                    for symbol in pair_symbols
                }
                no_cost = cash
                no_fee = cash
                actual = cash
                for leg in range(3):
                    no_cost = convert(no_cost, leg, open_prices, 0.0, 0.0)
                    no_fee = convert(no_fee, leg, open_prices, 0.0, self.slippage_rate)
                    actual = convert(actual, leg, open_prices, self.fee_rate, self.slippage_rate)
                if not all(math.isfinite(value) and value > 0 for value in (no_cost, no_fee, actual)):
                    return self._empty(f"invalid_triangle_conversion_at_index_{index}")
                fee_cost = no_fee - actual
                slip_cost = no_cost - no_fee
                profit = actual - cash
                cycles.append({
                    "signal_index": index, "fill_index": index + 1,
                    "signal_timestamp": signal_stamp, "fill_timestamp": fill_stamp,
                    "starting_amount": round(cash, 12),
                    "ending_amount": round(actual, 12),
                    "profit": round(profit, 12),
                    "profit_percent": round((profit / cash) * 100.0, 8),
                    "estimated_fees_in_start_asset": round(fee_cost, 12),
                    "estimated_slippage_in_start_asset": round(slip_cost, 12),
                    "signal_estimated_profit_percent": round(candidate_pct, 8),
                    "execution_prices": open_prices,
                })
                fees_paid += fee_cost
                slippage_cost += slip_cost
                cash = actual
            equity.append(cash)

        return {
            "ok": True, "strategy_key": "arbitrage",
            "route": route, "pair_symbols": pair_symbols,
            "total_trades": len(cycles), "profitable_cycles": sum(1 for item in cycles if item["profit"] > 0),
            "initial_cash": round(self.initial_cash, 12),
            "final_equity": round(cash, 12),
            "total_return": round((cash - self.initial_cash) / self.initial_cash, 8),
            "fees_paid": round(fees_paid, 12),
            "slippage_cost": round(slippage_cost, 12),
            "cycles": cycles, "equity_curve": [round(value, 12) for value in equity],
            "data_points": len(aligned), "min_profit_pct": threshold,
            "signal_generation": "synchronized_close_price_triangle_scan",
            "execution_model": "next_candle_open_three_leg_conversion_with_fee_and_slippage",
            "no_lookahead": True, "execution": "simulated_only", "no_live_orders": True,
            "limitations": [
                "uses genuine Binance historical OHLCV candles, not historical bid/ask quotes or order-book depth",
                "signals use current candle closes and execute at the next candle opens",
                "three-leg fills are assumed atomic at modeled prices; latency, partial fills and exchange filters are not modeled",
                "fees and slippage are estimates, not account-specific exchange fee tiers",
                "a candle-based profitable cycle is not proof of executable arbitrage",
            ],
        }

    def run_triangular_orderbook_replay(
        self,
        snapshots: Sequence[dict],
        *,
        route: list[str],
        min_profit_pct: float = 0.05,
    ) -> dict[str, Any]:
        """Replay synchronized, persisted full-depth Spot order-book snapshots."""
        if not isinstance(snapshots, Sequence) or isinstance(snapshots, (str, bytes)):
            return self._empty("orderbook_snapshots_must_be_a_sequence")
        if not 1 <= len(snapshots) <= 5000:
            return self._empty("orderbook_snapshot_count_out_of_range")
        if (not isinstance(route, list) or len(route) != 4
                or any(not isinstance(asset, str) or not asset.isascii()
                       or not asset.isalnum() for asset in route)
                or route[0] != route[-1] or len(set(route[:3])) != 3):
            return self._empty("triangle_route_must_be_three_distinct_assets_and_closed")
        if isinstance(min_profit_pct, bool):
            return self._empty("boolean_min_profit_threshold_not_allowed")
        try:
            threshold = float(min_profit_pct)
        except (TypeError, ValueError, OverflowError):
            return self._empty("invalid_min_profit_threshold")
        if not math.isfinite(threshold) or not 0 <= threshold <= 100:
            return self._empty("min_profit_threshold_out_of_range")

        def normalized_levels(raw: Any, *, descending: bool) -> list[tuple[float, float]]:
            if not isinstance(raw, list) or not raw:
                raise ValueError("missing_orderbook_depth_levels")
            levels = []
            for row in raw:
                if not isinstance(row, (list, tuple)) or len(row) < 2:
                    raise ValueError("invalid_orderbook_depth_level")
                price = self._number({"price": row[0]}, "price")
                quantity = self._number({"quantity": row[1]}, "quantity")
                if price <= 0 or quantity <= 0:
                    raise ValueError("non_positive_orderbook_depth_level")
                levels.append((price, quantity))
            if levels != sorted(levels, key=lambda item: item[0], reverse=descending):
                raise ValueError("orderbook_depth_levels_not_price_sorted")
            return levels

        def convert_depth(amount: float, levels: list[tuple[float, float]], *, sell_base: bool):
            remaining = amount
            output = 0.0
            consumed = []
            for price, quantity in levels:
                capacity = quantity if sell_base else quantity * price
                used = min(remaining, capacity)
                if used <= 0:
                    continue
                if sell_base:
                    base_used = used
                    quote_received = base_used * price
                    remaining -= base_used
                    output += quote_received
                    consumed.append({"price": price, "base_quantity": base_used, "quote_value": quote_received})
                else:
                    quote_spent = used
                    base_received = quote_spent / price
                    remaining -= quote_spent
                    output += base_received
                    consumed.append({"price": price, "base_quantity": base_received, "quote_value": quote_spent})
                if remaining <= max(1e-12, amount * 1e-12):
                    remaining = 0.0
                    break
            if remaining > max(1e-10, amount * 1e-9):
                return None
            fee = output * self.fee_rate
            return {"output_before_fee": output, "output_after_fee": output - fee,
                    "fee_paid_in_output_asset": fee, "levels_consumed": consumed}

        cash = self.initial_cash
        cycles = []
        rejected = []
        equity = [cash]
        previous_stamp = None
        for index, snapshot in enumerate(snapshots):
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get("quotes"), dict):
                return self._empty(f"invalid_orderbook_snapshot_{index}")
            raw_stamp = snapshot.get("timestamp")
            if isinstance(raw_stamp, bool) or raw_stamp is None:
                return self._empty("orderbook_snapshot_timestamp_required")
            try:
                stamp = float(raw_stamp)
            except (TypeError, ValueError, OverflowError):
                return self._empty("invalid_orderbook_snapshot_timestamp")
            if not math.isfinite(stamp) or stamp <= 0 or (previous_stamp is not None and stamp <= previous_stamp):
                return self._empty("orderbook_snapshot_timestamps_must_increase")
            previous_stamp = stamp
            quotes = {}
            for raw_symbol, quote in snapshot["quotes"].items():
                if not isinstance(raw_symbol, str) or not isinstance(quote, dict):
                    return self._empty(f"invalid_orderbook_quote_{index}")
                symbol = raw_symbol.replace("/", "").upper()
                if quote.get("source") != "binance_public_read_only":
                    return self._empty("verified_binance_spot_orderbook_required")
                quote_symbol = str(quote.get("symbol") or raw_symbol).replace("/", "").upper()
                if symbol != quote_symbol or not symbol.isalnum():
                    return self._empty("orderbook_symbol_mismatch")
                try:
                    bids = normalized_levels(quote.get("bids"), descending=True)
                    asks = normalized_levels(quote.get("asks"), descending=False)
                except (TypeError, ValueError, OverflowError) as exc:
                    return self._empty(f"invalid_orderbook_depth_{index}:{str(exc)[:100]}")
                if bids[0][0] >= asks[0][0]:
                    return self._empty("crossed_orderbook_snapshot")
                quotes[symbol] = {"bids": bids, "asks": asks}
            if len(quotes) != 3:
                return self._empty("exactly_three_orderbook_markets_required")

            amount = cash
            legs = []
            failed_leg = None
            for leg_index in range(3):
                source_asset, target_asset = route[leg_index], route[leg_index + 1]
                direct, inverse = source_asset + target_asset, target_asset + source_asset
                if direct in quotes:
                    result = convert_depth(amount, quotes[direct]["bids"], sell_base=True)
                    side, pair = "sell_base_into_bids", direct
                elif inverse in quotes:
                    result = convert_depth(amount, quotes[inverse]["asks"], sell_base=False)
                    side, pair = "buy_base_from_asks", inverse
                else:
                    return self._empty(f"missing_orderbook_market_for_leg_{leg_index}")
                if result is None:
                    failed_leg = leg_index
                    break
                legs.append({
                    "from_asset": source_asset, "to_asset": target_asset,
                    "market_symbol": pair, "execution_side": side,
                    "input_amount": round(amount, 12),
                    "output_before_fee": round(result["output_before_fee"], 12),
                    "output_after_fee": round(result["output_after_fee"], 12),
                    "fee_paid_in_output_asset": round(result["fee_paid_in_output_asset"], 12),
                    "levels_consumed": result["levels_consumed"],
                })
                amount = result["output_after_fee"]
            if failed_leg is not None:
                rejected.append({"snapshot_index": index, "timestamp": stamp,
                                 "reason": "insufficient_recorded_depth", "leg": failed_leg})
            else:
                profit = amount - cash
                profit_pct = (profit / cash) * 100.0
                if profit_pct > threshold:
                    cycles.append({
                        "snapshot_index": index, "timestamp": stamp,
                        "starting_amount": cash, "ending_amount": amount,
                        "profit": round(profit, 12), "profit_percent": round(profit_pct, 8),
                        "legs": legs,
                    })
                    cash = amount
            equity.append(cash)

        return {
            "ok": True, "strategy_key": "arbitrage", "market_type": "spot",
            "route": route, "total_snapshots": len(snapshots),
            "total_trades": len(cycles), "profitable_cycles": len(cycles),
            "rejected_insufficient_depth": len(rejected), "rejected": rejected,
            "initial_cash": self.initial_cash, "final_equity": round(cash, 12),
            "total_return": round((cash - self.initial_cash) / self.initial_cash, 8),
            "fees_paid": None, "slippage_cost": None,
            "cycles": cycles, "equity_curve": [round(value, 12) for value in equity],
            "execution_model": "historical_full_depth_walk_the_book_three_leg_simulation",
            "data_model": "persisted_synchronized_binance_spot_rest_depth_snapshots",
            "no_lookahead": True, "execution": "simulated_only", "no_live_orders": True,
            "limitations": [
                "historical depth exists only from the time TITAN began capturing snapshots; Binance public REST does not provide arbitrary past order-book depth",
                "snapshots are sequential REST captures, not a lossless WebSocket depth-diff reconstruction",
                "latency, queue priority, cancellations between capture points and exchange filters are not modeled",
                "fees are applied per leg using the configured estimate; account-specific fee tiers are not loaded",
                "a simulated depth-profitable cycle is not a guaranteed executable arbitrage",
            ],
        }

    def run_triangular_arbitrage_replay(
        self,
        snapshots: Sequence[dict],
        *,
        route: list[str],
        min_profit_pct: float = 0.05,
    ) -> dict[str, Any]:
        """Replay synchronized three-market quote snapshots through the arb analyzer."""
        if not isinstance(snapshots, Sequence) or isinstance(snapshots, (str, bytes)):
            return self._empty("arbitrage_snapshots_must_be_a_sequence")
        if not snapshots:
            return self._empty("at_least_one_arbitrage_snapshot_required")
        if not isinstance(route, list) or len(route) != 4:
            return self._empty("triangle_route_must_contain_four_assets")
        try:
            from app.engines.arbitrage import ArbitrageAnalysisEngine
            analyzer = ArbitrageAnalysisEngine()
            cash = self.initial_cash
            cycles = []
            equity = [cash]
            previous_timestamp = None
            for index, snapshot in enumerate(snapshots):
                if not isinstance(snapshot, dict) or not isinstance(snapshot.get("quotes"), dict):
                    return self._empty(f"invalid_arbitrage_snapshot_{index}")
                raw_stamp = snapshot.get("timestamp")
                if isinstance(raw_stamp, bool) or raw_stamp is None:
                    return self._empty("arbitrage_snapshot_timestamp_required")
                stamp_value = float(raw_stamp)
                if not math.isfinite(stamp_value):
                    return self._empty("invalid_arbitrage_snapshot_timestamp")
                if stamp_value > 10_000_000_000:
                    stamp_value /= 1000.0
                if previous_timestamp is not None and stamp_value <= previous_timestamp:
                    return self._empty("arbitrage_snapshot_timestamps_must_increase")
                previous_timestamp = stamp_value
                now = datetime.fromtimestamp(stamp_value, tz=timezone.utc)
                result = analyzer.analyze_triangle(
                    route, snapshot["quotes"], starting_amount=cash,
                    fee_rate=self.fee_rate, slippage_rate=self.slippage_rate,
                    min_profit_pct=min_profit_pct, now=now,
                )
                if not result.get("ok"):
                    return self._empty(f"arbitrage_snapshot_{index}:{result.get('error', 'invalid')}")
                if result.get("candidate") is True:
                    ending = float(result["ending_amount_estimate"])
                    cycles.append({
                        "snapshot_index": index, "timestamp": stamp_value,
                        "starting_amount": cash, "ending_amount": ending,
                        "profit": round(ending - cash, 12),
                        "profit_percent": result["profit_percent_estimate"],
                        "legs": result["legs"],
                    })
                    cash = ending
                equity.append(cash)
            return {
                "ok": True, "strategy_key": "arbitrage",
                "total_trades": len(cycles), "profitable_cycles": len(cycles),
                "initial_cash": self.initial_cash, "final_equity": round(cash, 12),
                "total_return": round((cash - self.initial_cash) / self.initial_cash, 8),
                "fees_paid": None, "slippage_cost": None,
                "cycles": cycles, "equity_curve": [round(value, 12) for value in equity],
                "execution_model": "synchronized_top_of_book_estimated_three_leg_conversion",
                "execution": "simulated_only", "no_live_orders": True,
                "no_lookahead": True,
                "limitations": [
                    "replays supplied historical quote snapshots; does not fetch or invent missing history",
                    "top-of-book depth, exchange filters, precision, latency and partial fills are not modeled",
                    "fees and slippage are configured estimates, not account-specific exchange rates",
                    "a profitable estimate is not an executable arbitrage guarantee",
                ],
            }
        except (TypeError, ValueError, KeyError, OverflowError) as exc:
            return self._empty("arbitrage_replay_failed:" + str(exc)[:160])


    def run_futures_orderbook_replay(
        self,
        snapshots: Sequence[dict[str, Any]],
        *,
        rolling_window: int = 5,
        entry_imbalance: float = 0.20,
        exit_imbalance: float = 0.02,
        leverage: float = 2.0,
        notional: float = 800.0,
        max_leverage: float = 10.0,
        funding_events: Sequence[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        """Replay recorded USD-M Futures depth snapshots; signals use only prior snapshots."""
        if not isinstance(snapshots, Sequence) or isinstance(snapshots, (str, bytes)):
            return self._empty("futures_orderbook_snapshots_must_be_sequence")
        if isinstance(rolling_window, bool) or not isinstance(rolling_window, int) or not 2 <= rolling_window <= 100:
            return self._empty("invalid_futures_orderbook_rolling_window")
        if len(snapshots) < rolling_window + 2 or len(snapshots) > 20000:
            return self._empty("futures_orderbook_snapshot_count_out_of_range")
        if any(isinstance(x, bool) for x in (entry_imbalance, exit_imbalance, leverage, notional, max_leverage)):
            return self._empty("boolean_futures_orderbook_parameter")
        try:
            entry=float(entry_imbalance); exit_=float(exit_imbalance)
            lev=float(leverage); size=float(notional); lev_cap=float(max_leverage)
        except (TypeError, ValueError, OverflowError):
            return self._empty("invalid_futures_orderbook_parameter")
        if (not all(math.isfinite(x) for x in (entry,exit_,lev,size,lev_cap))
                or not 0.05 <= entry <= 0.95 or not -0.5 <= exit_ <= 0.5
                or exit_ >= entry or lev < 1 or lev > min(lev_cap,10)
                or size <= 0 or size > 800 or lev_cap < 1 or lev_cap > 10):
            return self._empty("futures_orderbook_parameter_out_of_range")
        normalized=[];symbol=None;previous=-1
        for i,snapshot in enumerate(snapshots):
            if not isinstance(snapshot,dict) or not isinstance(snapshot.get("quotes"),dict):
                return self._empty(f"invalid_futures_orderbook_snapshot_{i}")
            quotes=snapshot["quotes"]
            quote=quotes.get("symbol") if isinstance(quotes.get("symbol"),dict) else quotes
            if not isinstance(quote,dict):
                return self._empty(f"invalid_futures_orderbook_quote_{i}")
            try:
                stamp=int(snapshot.get("timestamp",quote.get("timestamp",0)))
                bid=float(quote["best_bid"]);ask=float(quote["best_ask"])
                bid_depth=float(quote["bid_depth_quote"]);ask_depth=float(quote["ask_depth_quote"])
                imbalance=float(quote["depth_imbalance"])
                event_symbol=str(quote.get("symbol") or snapshot.get("symbol") or "").upper()
            except (KeyError,TypeError,ValueError,OverflowError):
                return self._empty(f"invalid_futures_orderbook_fields_{i}")
            if (stamp<=previous or not event_symbol or bid<=0 or ask<=bid
                    or bid_depth<=0 or ask_depth<=0 or not all(math.isfinite(x) for x in (bid,ask,bid_depth,ask_depth,imbalance))
                    or not -1<=imbalance<=1):
                return self._empty(f"invalid_futures_orderbook_values_{i}")
            if symbol is None: symbol=event_symbol
            if event_symbol!=symbol: return self._empty("futures_orderbook_symbol_mismatch")
            normalized.append({"timestamp":stamp,"symbol":symbol,"bid":bid,"ask":ask,
                "mid":(bid+ask)/2,"bid_depth":bid_depth,"ask_depth":ask_depth,"imbalance":imbalance})
            previous=stamp
        from app.engines.futures_backtest import FuturesBacktestEngine
        candles=[];signals=[];active=None
        for i,event in enumerate(normalized):
            mid=event["mid"]
            candles.append({"timestamp":event["timestamp"],"open":mid,"high":event["ask"],
                "low":event["bid"],"close":mid,"volume":event["bid_depth"]+event["ask_depth"]})
            if i<rolling_window or i>=len(normalized)-1: continue
            window=normalized[i-rolling_window+1:i+1]
            imbalance=sum(x["imbalance"] for x in window)/rolling_window
            next_book=normalized[i+1]
            if active is None and imbalance>=entry:
                available=min(next_book["ask_depth"],size)
                if available>=min(size,10.0):
                    signals.append({"index":i,"action":"long","leverage":lev,"notional":min(size,available)})
                    active="long"
            elif active is None and imbalance<=-entry:
                available=min(next_book["bid_depth"],size)
                if available>=min(size,10.0):
                    signals.append({"index":i,"action":"short","leverage":lev,"notional":min(size,available)})
                    active="short"
            elif active=="long" and imbalance<=exit_:
                signals.append({"index":i,"action":"close"});active=None
            elif active=="short" and imbalance>=-exit_:
                signals.append({"index":i,"action":"close"});active=None
        engine=FuturesBacktestEngine(initial_cash=self.initial_cash,fee_rate=self.fee_rate,
            slippage_rate=self.slippage_rate,max_notional=min(size,800.0),max_leverage=lev_cap)
        result=engine.run(candles,signals,funding_events=funding_events)
        result.update({"strategy_key":"liquidity_orderflow","market_type":"futures",
            "symbol":symbol,"signal_generation":"rolling_historical_depth_imbalance",
            "execution_model":"next_snapshot_mid_with_configured_slippage_and_depth_notional_cap",
            "depth_snapshots":len(normalized),"generated_signals":len(signals),
            "rolling_window":rolling_window,"entry_imbalance":entry,"exit_imbalance":exit_,
            "no_lookahead":True,"execution":"simulated_only","no_live_orders":True,
            "data_source":"titan_recorded_binance_usdm_futures_depth",
            "limitations":["snapshots are recorded prospectively; Binance REST does not provide retroactive historical depth snapshots",
                "REST snapshots do not reconstruct every incremental depth update between captures",
                "queue priority and exact partial fills are approximated by snapshot depth notional caps",
                "funding costs are included only when historical funding events are supplied"]})
        return result


    def run_triangular_execution_replay(
        self,
        snapshots: Sequence[dict],
        *,
        route: list[str],
        min_profit_pct: float = 0.05,
        latency_ms: int = 250,
        symbol_filters: Mapping[str, dict] | None = None,
        max_cycles: int = 1000,
    ) -> dict[str, Any]:
        """Latency-aware three-leg replay with book walking, lot filters and partial-fill reporting."""
        if not isinstance(snapshots, Sequence) or isinstance(snapshots, (str, bytes)) or not 2 <= len(snapshots) <= 5000:
            return self._empty("execution_snapshots_must_contain_2_to_5000_rows")
        if (not isinstance(route,list) or len(route)!=4 or route[0]!=route[-1]
                or len(set(route[:3]))!=3 or any(not isinstance(x,str) or not x.isascii() or not x.isalnum() for x in route)):
            return self._empty("invalid_triangle_route")
        if isinstance(latency_ms,bool) or not isinstance(latency_ms,int) or not 0<=latency_ms<=60000:
            return self._empty("latency_ms_out_of_range")
        if isinstance(max_cycles,bool) or not isinstance(max_cycles,int) or not 1<=max_cycles<=10000:
            return self._empty("max_cycles_out_of_range")
        if isinstance(min_profit_pct,bool):
            return self._empty("boolean_min_profit_threshold_not_allowed")
        try: threshold=float(min_profit_pct)
        except (TypeError,ValueError,OverflowError): return self._empty("invalid_min_profit_threshold")
        if not math.isfinite(threshold) or not 0<=threshold<=100:
            return self._empty("min_profit_threshold_out_of_range")
        if symbol_filters is not None and not isinstance(symbol_filters, Mapping):
            return self._empty("symbol_filters_must_be_a_mapping")
        filters = dict(symbol_filters or {})
        if any(not isinstance(key, str) or not key.isascii() or not key.isalnum()
               for key in filters):
            return self._empty("invalid_symbol_filter_key")

        def normalize_depth(raw):
            if not isinstance(raw, list) or not raw:
                raise ValueError("missing_depth_levels")
            levels = []
            for row in raw:
                if (not isinstance(row, (list, tuple)) or len(row) != 2
                        or any(isinstance(value, bool) for value in row)):
                    raise ValueError("invalid_depth_level")
                price, quantity = float(row[0]), float(row[1])
                if (not math.isfinite(price) or not math.isfinite(quantity)
                        or price <= 0 or quantity <= 0):
                    raise ValueError("invalid_depth_level_values")
                levels.append((price, quantity))
            return levels

        normalized=[];prev=-1
        for i,snap in enumerate(snapshots):
            if not isinstance(snap,dict) or not isinstance(snap.get("quotes"),dict):
                return self._empty(f"invalid_execution_snapshot_{i}")
            raw_stamp = snap.get("timestamp")
            if isinstance(raw_stamp, bool) or raw_stamp is None:
                return self._empty(f"invalid_execution_snapshot_timestamp_{i}")
            try:
                numeric_stamp = float(raw_stamp)
            except (TypeError, ValueError, OverflowError):
                return self._empty(f"invalid_execution_snapshot_timestamp_{i}")
            if (not math.isfinite(numeric_stamp) or not numeric_stamp.is_integer()
                    or numeric_stamp <= 0):
                return self._empty(f"invalid_execution_snapshot_timestamp_{i}")
            stamp = int(numeric_stamp)
            if stamp<=prev: return self._empty("execution_snapshot_timestamps_must_increase")
            prev=stamp; quotes={}
            for raw,quote in snap["quotes"].items():
                symbol=str(raw).replace("/","").upper()
                if not isinstance(quote,dict) or quote.get("source")!="binance_public_read_only":
                    return self._empty("verified_spot_depth_snapshots_required")
                try:
                    bids=normalize_depth(quote["bids"])
                    asks=normalize_depth(quote["asks"])
                except (KeyError,TypeError,ValueError,IndexError,OverflowError):
                    return self._empty(f"invalid_depth_levels_{i}_{symbol}")
                if (bids!=sorted(bids,key=lambda x:x[0],reverse=True)
                        or asks!=sorted(asks,key=lambda x:x[0]) or bids[0][0]>=asks[0][0]):
                    return self._empty(f"invalid_or_crossed_depth_{i}_{symbol}")
                quotes[symbol]={"bids":bids,"asks":asks}
            if len(quotes)!=3: return self._empty("exactly_three_markets_required_per_snapshot")
            normalized.append({"timestamp":stamp,"quotes":quotes})

        def filter_for(symbol):
            f=filters.get(symbol,{})
            if not isinstance(f,dict): raise ValueError("invalid_symbol_filter")
            vals={}
            for key,default in (("tick_size",0.0),("step_size",0.0),
                                ("min_qty",0.0),("min_notional",0.0),("max_qty",0.0)):
                raw=f.get(key,default)
                if isinstance(raw,bool): raise ValueError("boolean_symbol_filter")
                value=float(raw)
                if not math.isfinite(value) or value<0: raise ValueError("invalid_symbol_filter")
                vals[key]=value
            return vals

        def floor_step(value,step):
            return value if step<=0 else math.floor((value+1e-12)/step)*step

        for symbol in filters:
            try:
                filter_for(symbol)
            except (TypeError, ValueError, OverflowError) as exc:
                return self._empty("exchange_filter_error:" + str(exc)[:100])

        def execute_leg(amount, symbol, side, book, output_asset, quote_asset):
            f=filter_for(symbol)
            levels=book["bids"] if side=="sell_base" else book["asks"]
            remaining=amount; output=0.0; consumed=[]; filled_input=0.0
            total_quantity=0.0; filled_notional=0.0; slippage_cost=0.0
            for price,available in levels:
                if f["tick_size"] > 0 and abs(price / f["tick_size"] - round(price / f["tick_size"])) > 1e-7:
                    raise ValueError("book_price_violates_tick_size")
                max_remaining = (float("inf") if f["max_qty"] <= 0
                                 else max(0.0, f["max_qty"] - total_quantity))
                execution_price = price * (1.0 + self.slippage_rate if side=="buy_base"
                                            else 1.0 - self.slippage_rate)
                if side=="sell_base":
                    qty=floor_step(min(remaining,available,max_remaining),f["step_size"])
                    if qty<=0: continue
                    notional=qty*execution_price
                    level_slippage=qty*(price-execution_price)
                    output+=notional;remaining-=qty;filled_input+=qty
                else:
                    spend=min(remaining,available*execution_price)
                    qty=floor_step(min(spend/execution_price,max_remaining),f["step_size"])
                    if qty<=0: continue
                    notional=qty*execution_price
                    if notional > remaining + max(1e-12,amount*1e-12):
                        continue
                    level_slippage=qty*(execution_price-price)
                    output+=qty;remaining-=notional;filled_input+=notional
                total_quantity+=qty;filled_notional+=notional;slippage_cost+=level_slippage
                consumed.append({"price":price,"execution_price":execution_price,
                    "quantity":qty,"notional":notional,"slippage_cost":level_slippage})
                if remaining<=max(1e-12,amount*1e-10): remaining=0.0;break
            fee=output*self.fee_rate
            rounding_dust = (f["step_size"] if side=="sell_base" else
                              f["step_size"] * (levels[-1][0] if levels else 0.0))
            tolerance=max(1e-10,amount*1e-9,rounding_dust)
            filter_rejection = None
            if f["min_qty"] and total_quantity + 1e-12 < f["min_qty"]:
                filter_rejection = "exchange_min_qty_not_met"
            elif f["min_notional"] and filled_notional + 1e-12 < f["min_notional"]:
                filter_rejection = "exchange_min_notional_not_met"
            elif f["max_qty"] and total_quantity >= f["max_qty"] - 1e-12 and remaining > tolerance:
                filter_rejection = "exchange_max_qty_reached"
            depth_complete = remaining <= tolerance
            complete = depth_complete and filter_rejection is None
            rejection_reason = filter_rejection or (None if depth_complete else "insufficient_depth")
            return {"output":output-fee,"output_before_fee":output,"fee":fee,
                "fee_asset":output_asset,"slippage_cost":slippage_cost,
                "slippage_asset":quote_asset,"filled_input":filled_input,
                "unfilled_input":max(0.0,remaining),"filled_quantity":total_quantity,
                "filled_notional":filled_notional,"rounding_dust_tolerance":rounding_dust,
                "complete":complete,"rejection_reason":rejection_reason,"levels":consumed}


        cash=self.initial_cash;cycles=[];rejected=[];partial=[];equity=[cash];i=0
        fees_by_asset={};slippage_by_asset={}
        while i<len(normalized)-1 and len(cycles)<max_cycles:
            signal=normalized[i]
            # Use only current snapshot to qualify the opportunity.
            probe=cash
            valid=True
            for leg in range(3):
                a,b=route[leg],route[leg+1]
                direct,inverse=a+b,b+a
                if direct in signal["quotes"]:
                    levels=signal["quotes"][direct]["bids"]
                    px=levels[0][0];probe*=px*(1-self.slippage_rate)*(1-self.fee_rate)
                elif inverse in signal["quotes"]:
                    levels=signal["quotes"][inverse]["asks"]
                    px=levels[0][0];probe=probe/(px*(1+self.slippage_rate))*(1-self.fee_rate)
                else: valid=False;break
            if not valid:
                return self._empty("missing_market_for_triangle_leg")
            estimated=(probe/cash-1)*100 if cash>0 else -100
            if estimated<=threshold:
                equity.append(cash);i+=1;continue
            fill_index=next((j for j in range(i+1,len(normalized))
                if normalized[j]["timestamp"]>=signal["timestamp"]+latency_ms),None)
            if fill_index is None:
                rejected.append({"signal_index":i,"reason":"latency_exceeds_remaining_history"})
                equity.append(cash);i+=1;continue
            book=normalized[fill_index]["quotes"];amount=cash;legs=[];cycle_ok=True
            for leg in range(3):
                a,b=route[leg],route[leg+1];direct,inverse=a+b,b+a
                if direct in book: symbol=direct;side="sell_base"
                elif inverse in book: symbol=inverse;side="buy_base"
                else: return self._empty(f"missing_fill_market_for_leg_{leg}")
                output_asset = b
                quote_asset = b if side=="sell_base" else a
                try: result=execute_leg(amount,symbol,side,book[symbol],output_asset,quote_asset)
                except (TypeError,ValueError,OverflowError) as exc:
                    return self._empty("exchange_filter_error:"+str(exc)[:100])
                fees_by_asset[result["fee_asset"]] = fees_by_asset.get(result["fee_asset"],0.0)+result["fee"]
                slippage_by_asset[result["slippage_asset"]] = slippage_by_asset.get(result["slippage_asset"],0.0)+result["slippage_cost"]
                legs.append({"leg":leg+1,"symbol":symbol,"side":side,**result})
                if not result["complete"]:
                    partial.append({"signal_index":i,"fill_index":fill_index,"timestamp":normalized[fill_index]["timestamp"],
                        "failed_leg":leg+1,"symbol":symbol,"filled_input":result["filled_input"],
                        "filled_quantity":result["filled_quantity"],"filled_notional":result["filled_notional"],
                        "unfilled_input":result["unfilled_input"],"unhedged_output":result["output"],
                        "reason":result["rejection_reason"] or "partial_depth_fill"})
                    cycle_ok=False;break
                amount=result["output"]
            if cycle_ok:
                profit=amount-cash;profit_pct=profit/cash*100 if cash else -100
                cycles.append({"signal_index":i,"fill_index":fill_index,
                    "signal_timestamp":signal["timestamp"],"fill_timestamp":normalized[fill_index]["timestamp"],
                    "latency_ms":normalized[fill_index]["timestamp"]-signal["timestamp"],
                    "starting_amount":cash,"ending_amount":amount,"profit":profit,
                    "profit_percent":profit_pct,"estimated_signal_profit_pct":estimated,"legs":legs})
                cash=amount
            else:
                rejected.append({"signal_index":i,"fill_index":fill_index,"reason":"incomplete_three_leg_cycle"})
            equity.append(cash);i=fill_index+1
        return {"ok":True,"strategy_key":"arbitrage","market_type":"spot","route":route,
            "total_snapshots":len(normalized),"total_trades":len(cycles),"profitable_cycles":sum(x["profit"]>0 for x in cycles),
            "rejected_cycles":len(rejected),"partial_fills":len(partial),"rejected":rejected,"partial_fill_details":partial,
            "initial_cash":self.initial_cash,"final_equity":round(cash,12),
            "total_return":round(cash/self.initial_cash-1,8),"cycles":cycles,"equity_curve":equity,
            "execution_model":"latency_aware_next_snapshot_depth_walk_with_step_min_qty_min_notional_and_partial_fill_detection",
            "latency_ms_configured":latency_ms,"exchange_filters_supplied":bool(filters),
            "fee_rate_per_leg":self.fee_rate,"fees_paid_by_asset":{k:round(v,12) for k,v in fees_by_asset.items()},
            "slippage_rate_configured":self.slippage_rate,
            "slippage_cost_by_quote_asset":{k:round(v,12) for k,v in slippage_by_asset.items()},
            "slippage_model":"historical_book_depth_plus_configured_adverse_fill_rate",
            "no_lookahead":True,"execution":"simulated_only","no_live_orders":True,
            "limitations":["REST snapshot intervals can miss book changes during configured latency",
                "partial fills are reported as unhedged exposure; this offline model does not submit hedge orders",
                "tick sizes are validated as supplied metadata but recorded book prices already reflect exchange ticks",
                "maker queue priority and account-specific fee tiers are not available",
                "symbol filters must be supplied from exchangeInfo for the historical date; current filters may differ"]}
