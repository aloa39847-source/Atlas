"""Deterministic, demo-only replay of pre-trade risk gates and realized outcomes.

This simulator never submits orders. It reuses RiskEngine for each proposed trade,
tracks realized daily loss, and records rejected candidates without applying their PnL.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

from app.core.enums import EnvironmentMode, MarketType
from app.core.config import settings
from app.engines.risk import RiskEngine


class RiskSimulationEngine:
    """Replay a sequence of closed hypothetical trades through the risk gate."""

    def __init__(self, risk_engine: RiskEngine | None = None):
        self.risk_engine = risk_engine or RiskEngine()

    def run(
        self,
        trades: Sequence[dict[str, Any]],
        *,
        initial_balance: float,
        market: MarketType,
        risk_per_trade: float = 0.005,
        max_daily_loss: float = 0.07,
        max_open_positions: int = 1,
        max_exposure: float | None = None,
    ) -> dict[str, Any]:
        if not isinstance(market, MarketType) or market not in {MarketType.SPOT, MarketType.FUTURES}:
            return {"ok": False, "error": "spot_or_futures_market_required", "no_live_orders": True}
        if not isinstance(trades, Sequence) or isinstance(trades, (str, bytes)):
            return {"ok": False, "error": "trades_must_be_a_sequence", "no_live_orders": True}
        if any(isinstance(value, bool) for value in (initial_balance, risk_per_trade, max_daily_loss)):
            return {"ok": False, "error": "boolean_simulation_parameter_not_allowed", "no_live_orders": True}
        try:
            balance = float(initial_balance)
            risk_fraction = float(risk_per_trade)
            daily_limit = float(max_daily_loss)
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "invalid_simulation_parameters", "no_live_orders": True}
        if (not all(math.isfinite(v) for v in (balance, risk_fraction, daily_limit))
                or balance <= 0 or risk_fraction <= 0 or not 0 <= daily_limit <= 1):
            return {"ok": False, "error": "simulation_parameters_out_of_range", "no_live_orders": True}
        if isinstance(max_open_positions, bool) or not isinstance(max_open_positions, int) or max_open_positions < 0:
            return {"ok": False, "error": "invalid_max_open_positions", "no_live_orders": True}
        if max_exposure is not None:
            if isinstance(max_exposure, bool):
                return {"ok": False, "error": "invalid_max_exposure", "no_live_orders": True}
            try:
                max_exposure = float(max_exposure)
            except (TypeError, ValueError, OverflowError):
                return {"ok": False, "error": "invalid_max_exposure", "no_live_orders": True}
            if not math.isfinite(max_exposure) or max_exposure < 0:
                return {"ok": False, "error": "invalid_max_exposure", "no_live_orders": True}

        equity = balance
        peak_equity = equity
        max_drawdown = 0.0
        day_key = None
        day_start_balance = equity
        daily_loss_used = 0.0
        accepted = rejected = 0
        rows: list[dict[str, Any]] = []
        for index, trade in enumerate(trades):
            if not isinstance(trade, dict):
                return {"ok": False, "error": f"trade_{index}_must_be_object", "no_live_orders": True}
            session = str(trade.get("session", "default"))
            if session != day_key:
                day_key = session
                day_start_balance = equity
                daily_loss_used = 0.0
            try:
                if any(isinstance(trade.get(key, default), bool) for key, default in (
                    ("entry", None), ("stop_loss", None), ("leverage", 1.0),
                    ("realized_pnl", 0.0), ("current_exposure", 0.0),
                )):
                    raise ValueError("boolean_trade_numeric_field")
                entry = float(trade["entry"])
                stop = float(trade["stop_loss"])
                leverage = float(trade.get("leverage", 1.0))
                realized = float(trade.get("realized_pnl", 0.0))
                open_positions = trade.get("open_positions", 0)
                raw_exposure = trade.get("current_exposure", 0.0)
                if isinstance(raw_exposure, bool):
                    raise ValueError("boolean_current_exposure")
                current_exposure = float(raw_exposure)
            except (KeyError, TypeError, ValueError, OverflowError):
                return {"ok": False, "error": f"trade_{index}_invalid_numeric_fields", "no_live_orders": True}
            if not all(math.isfinite(v) for v in (entry, stop, leverage, realized, current_exposure)):
                return {"ok": False, "error": f"trade_{index}_non_finite_fields", "no_live_orders": True}
            if current_exposure < 0:
                return {"ok": False, "error": f"trade_{index}_invalid_current_exposure", "no_live_orders": True}
            if realized < 0 and equity + realized < 0:
                return {"ok": False, "error": f"trade_{index}_loss_exceeds_equity", "no_live_orders": True}

            gate = self.risk_engine.validate_trade(
                environment=EnvironmentMode.DEMO,
                market=market,
                balance=equity,
                risk_per_trade=risk_fraction,
                entry=entry,
                stop_loss=stop,
                leverage=leverage,
                position_side=str(trade.get("side", "long")),
                daily_loss_used=daily_loss_used,
                max_daily_loss=daily_limit,
                open_positions=open_positions,
                max_open_positions=max_open_positions,
                current_exposure=current_exposure,
                max_exposure=max_exposure,
            )
            if not gate.get("ok"):
                rejected += 1
                rows.append({"index": index, "session": session, "accepted": False,
                             "issues": gate.get("issues", []), "equity_after": round(equity, 8)})
                continue

            accepted += 1
            equity += realized
            if realized < 0:
                daily_loss_used += abs(realized)
            peak_equity = max(peak_equity, equity)
            max_drawdown = max(max_drawdown, peak_equity - equity)
            rows.append({"index": index, "session": session, "accepted": True,
                         "position_notional": gate.get("position_notional_estimate", 0.0),
                         "realized_pnl": round(realized, 8), "daily_loss_used": round(daily_loss_used, 8),
                         "equity_after": round(equity, 8), "issues": gate.get("issues", [])})

        return {
            "ok": True,
            "market": market.value,
            "initial_balance": round(balance, 8),
            "final_equity": round(equity, 8),
            "net_pnl": round(equity - balance, 8),
            "max_drawdown_quote": round(max_drawdown, 8),
            "accepted_trades": accepted,
            "rejected_trades": rejected,
            "results": rows,
            "execution": "simulation_only",
            "environment": EnvironmentMode.DEMO.value,
            "no_live_orders": True,
            "note": "Daily loss is accumulated from realized losing trades and resets when session changes.",
        }


    def run_historical_price_path(
        self,
        candles: Sequence[dict[str, Any]],
        *,
        initial_balance: float,
        market: MarketType,
        risk_per_trade: float = 0.005,
        max_daily_loss: float = 0.07,
        fee_rate: float = 0.001,
        slippage_rate: float = 0.0005,
        max_notional: float | None = None,
        leverage: float = 1.0,
        funding_events: Sequence[dict[str, Any]] = (),
        max_trades: int = 1000,
        session_ms: int = 86_400_000,
        maintenance_margin_rate: float = 0.005,
        liquidation_fee_rate: float = 0.005,
    ) -> dict[str, Any]:
        """Replay OHLCV risk rules with next-open entries, bracket exits and Futures funding."""
        if max_notional is None:
            max_notional = (settings.max_spot_trade_notional if market == MarketType.SPOT
                            else settings.max_futures_trade_notional)
        if not isinstance(candles, Sequence) or isinstance(candles, (str, bytes)):
            return {"ok": False, "error": "candles_must_be_a_sequence", "no_live_orders": True}
        numeric = (initial_balance, risk_per_trade, max_daily_loss, fee_rate,
                   slippage_rate, max_notional, leverage, maintenance_margin_rate,
                   liquidation_fee_rate)
        if any(isinstance(v, bool) for v in numeric):
            return {"ok": False, "error": "boolean_price_path_parameter", "no_live_orders": True}
        try:
            balance, risk_fraction, daily_limit, fee, slip, cap, lev, mmr, liq_fee_rate = map(float, numeric)
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "invalid_price_path_parameters", "no_live_orders": True}
        if (not all(math.isfinite(v) for v in (balance, risk_fraction, daily_limit, fee, slip, cap, lev))
                or balance <= 0 or not 0 < risk_fraction <= 0.1
                or not 0 <= daily_limit <= 1 or not 0 <= fee < 0.1
                or not 0 <= slip < 0.1 or cap <= 0 or not 1 <= lev <= 10
                or not 0 <= mmr < 0.1 or not 0 <= liq_fee_rate < 0.1
                or (market == MarketType.FUTURES and mmr >= 1.0 / lev)
                or (market == MarketType.SPOT and lev != 1)
                or isinstance(max_trades, bool) or not isinstance(max_trades, int)
                or not 1 <= max_trades <= 10000
                or isinstance(session_ms, bool) or not isinstance(session_ms, int) or session_ms <= 0):
            return {"ok": False, "error": "price_path_parameters_out_of_range", "no_live_orders": True}
        if not isinstance(market, MarketType) or market not in {MarketType.SPOT, MarketType.FUTURES}:
            return {"ok": False, "error": "spot_or_futures_market_required", "no_live_orders": True}
        cap = min(cap, 80.0 if market == MarketType.SPOT else 800.0)
        if not isinstance(funding_events, Sequence) or isinstance(funding_events, (str, bytes)) or len(funding_events) > 10000:
            return {"ok": False, "error": "invalid_funding_event_sequence", "no_live_orders": True}

        rows = []
        prev_stamp = -1
        for index, row in enumerate(candles):
            if not isinstance(row, dict):
                return {"ok": False, "error": f"invalid_candle_{index}", "no_live_orders": True}
            try:
                vals = [float(row[k]) for k in ("open", "high", "low", "close", "volume")]
                raw_stamp = row.get("timestamp", row.get("open_time", index))
                if isinstance(raw_stamp, bool):
                    raise ValueError("boolean_timestamp")
                numeric_stamp = float(raw_stamp)
                if not math.isfinite(numeric_stamp) or not numeric_stamp.is_integer():
                    raise ValueError("timestamp_must_be_a_finite_integer")
                stamp = int(numeric_stamp)
            except (KeyError, TypeError, ValueError, OverflowError):
                return {"ok": False, "error": f"invalid_candle_fields_{index}", "no_live_orders": True}
            o, h, l, close, volume = vals
            if (not all(math.isfinite(x) for x in vals) or min(o,h,l,close) <= 0 or volume < 0
                    or h < max(o,close) or l > min(o,close) or h < l or stamp <= prev_stamp):
                return {"ok": False, "error": f"invalid_or_unsorted_candle_{index}", "no_live_orders": True}
            prev_stamp = stamp
            rows.append({"open":o,"high":h,"low":l,"close":close,"volume":volume,"timestamp":stamp})
        if len(rows) < 30 or len(rows) > 100000:
            return {"ok": False, "error": "historical_candle_count_out_of_range", "no_live_orders": True}

        funding=[];last_funding=-1
        for index,event in enumerate(funding_events):
            if not isinstance(event,dict) or any(isinstance(event.get(k),bool) for k in ("timestamp","rate")):
                return {"ok":False,"error":f"invalid_funding_event_{index}","no_live_orders":True}
            try:
                raw_stamp = event["timestamp"]
                if isinstance(raw_stamp, bool):
                    raise ValueError("boolean_timestamp")
                numeric_stamp = float(raw_stamp)
                rate = float(event["rate"])
                if not math.isfinite(numeric_stamp) or not numeric_stamp.is_integer():
                    raise ValueError("timestamp_must_be_a_finite_integer")
                stamp = int(numeric_stamp)
            except (KeyError, TypeError, ValueError, OverflowError):
                return {"ok": False, "error": f"invalid_funding_event_{index}", "no_live_orders": True}
            if stamp<=last_funding or not math.isfinite(rate) or abs(rate)>0.05:
                return {"ok":False,"error":f"invalid_or_unsorted_funding_event_{index}","no_live_orders":True}
            funding.append({"timestamp":stamp,"rate":rate});last_funding=stamp
        if market == MarketType.SPOT and funding:
            return {"ok":False,"error":"spot_funding_events_not_supported","no_live_orders":True}

        equity=balance;peak=balance;max_dd=0.0;daily_loss=0.0;day=None
        position=None;funding_cursor=0;funding_net_total=0.0
        trades_out=[];equity_curve=[balance];rejected_daily=0
        fee_total=0.0;slippage_total=0.0
        for i,row in enumerate(rows):
            # Apply each historical funding event once while a Futures position is open.
            while funding_cursor<len(funding) and funding[funding_cursor]["timestamp"]<=row["timestamp"]:
                event=funding[funding_cursor]
                if position is not None and event["timestamp"]>position["timestamp"]:
                    payment=-position["direction"]*position["qty"]*row["close"]*event["rate"]
                    equity+=payment;position["funding_net"]+=payment;funding_net_total+=payment
                funding_cursor+=1

            if position is not None:
                current_day=row["timestamp"]//session_ms
                if day is not None and current_day!=day:
                    daily_loss=0.0
                day=current_day
                long_side=position["direction"]>0
                stop_hit=row["low"]<=position["stop"] if long_side else row["high"]>=position["stop"]
                target_hit=row["high"]>=position["target"] if long_side else row["low"]<=position["target"]
                liquidation_price=position.get("liquidation_price")
                liquidation_hit=(
                    market == MarketType.FUTURES and liquidation_price is not None
                    and (row["low"]<=liquidation_price if long_side else row["high"]>=liquidation_price)
                )
                exit_raw=None;reason=None
                # OHLC cannot reveal intrabar event order. If liquidation and a stop
                # are both touched in one candle, assume liquidation first (conservative).
                if liquidation_hit:
                    exit_raw=min(row["open"],liquidation_price) if long_side else max(row["open"],liquidation_price)
                    reason="liquidation"
                elif stop_hit:
                    exit_raw=min(row["open"],position["stop"]) if long_side else max(row["open"],position["stop"])
                    reason="stop_loss"
                elif target_hit:
                    exit_raw=max(row["open"],position["target"]) if long_side else min(row["open"],position["target"])
                    reason="take_profit"
                if exit_raw is not None:
                    exit_price=exit_raw*(1.0-slip if long_side else 1.0+slip)
                    gross=position["direction"]*position["qty"]*(exit_price-position["entry"])
                    exit_fee=position["qty"]*exit_price*fee
                    liquidation_fee=(
                        min(position["qty"]*exit_price*liq_fee_rate,
                            position["qty"]*exit_price*mmr)
                        if reason=="liquidation" else 0.0
                    )
                    net=gross-position["entry_fee"]-exit_fee-liquidation_fee+position["funding_net"]
                    equity+=gross-exit_fee-liquidation_fee;fee_total+=exit_fee+liquidation_fee
                    slippage_total+=position["qty"]*abs(exit_raw-exit_price)
                    if net<0: daily_loss+=abs(net)
                    trades_out.append({"side":position["side"],"entry_timestamp":position["timestamp"],
                        "exit_timestamp":row["timestamp"],"entry_price":position["entry"],
                        "exit_price":exit_price,"quantity":position["qty"],"leverage":lev,
                        "liquidation_price":liquidation_price,"initial_margin":position.get("initial_margin"),
                        "liquidation_fee":round(liquidation_fee,10),
                        "gross_pnl":gross,"fees":position["entry_fee"]+exit_fee+liquidation_fee,
                        "funding_net":position["funding_net"],"net_pnl":net,"exit_reason":reason})
                    position=None
                    peak=max(peak,equity);max_dd=max(max_dd,peak-equity)

            if position is None and i>=20 and i<len(rows)-1 and len(trades_out)<max_trades:
                signal_day=row["timestamp"]//session_ms
                if day!=signal_day:
                    day=signal_day;daily_loss=0.0
                if daily_limit>0 and daily_loss>=equity*daily_limit:
                    rejected_daily+=1
                else:
                    prior=rows[max(0,i-14):i+1]
                    trs=[]
                    for j,x in enumerate(prior):
                        prev_close=prior[j-1]["close"] if j else x["close"]
                        trs.append(max(x["high"]-x["low"],abs(x["high"]-prev_close),abs(x["low"]-prev_close)))
                    atr=sum(trs[-14:])/min(14,len(trs))
                    fast=sum(x["close"] for x in rows[i-8:i+1])/9
                    slow=sum(x["close"] for x in rows[i-20:i+1])/21
                    direction=1 if fast>slow else -1 if fast<slow and market==MarketType.FUTURES else 0
                    if direction and atr>0:
                        raw_entry=rows[i+1]["open"]
                        entry=raw_entry*(1.0+slip if direction>0 else 1.0-slip)
                        stop=entry-direction*2.0*atr
                        target=entry+direction*3.0*atr
                        risk_cash=equity*risk_fraction
                        qty=min(risk_cash/max(2.0*atr,1e-12),cap/entry)
                        if market==MarketType.FUTURES:
                            qty=min(qty,(equity*lev)/entry)
                        else:
                            qty=min(qty,equity/entry)
                        entry_fee=qty*entry*fee
                        required_margin=qty*entry/lev if market==MarketType.FUTURES else qty*entry
                        if qty>0 and required_margin+entry_fee<=equity:
                            equity-=entry_fee;fee_total+=entry_fee
                            slippage_total+=qty*abs(entry-raw_entry)
                            liquidation_price = None
                            if market == MarketType.FUTURES:
                                liquidation_price = entry * (
                                    1.0 - 1.0 / lev + mmr if direction > 0
                                    else 1.0 + 1.0 / lev - mmr
                                )
                            position={"side":"long" if direction>0 else "short","direction":direction,
                                "entry":entry,"stop":stop,"target":target,"qty":qty,
                                "entry_fee":entry_fee,"funding_net":0.0,"timestamp":rows[i+1]["timestamp"],
                                "initial_margin":required_margin,"liquidation_price":liquidation_price}

            mark=equity
            if position is not None and row["timestamp"]>=position["timestamp"]:
                mark+=position["direction"]*position["qty"]*(row["close"]-position["entry"])
            equity_curve.append(mark)
            peak=max(peak,mark);max_dd=max(max_dd,peak-mark)

        if position is not None:
            row=rows[-1];raw=row["close"];long_side=position["direction"]>0
            exit_price=raw*(1.0-slip if long_side else 1.0+slip)
            gross=position["direction"]*position["qty"]*(exit_price-position["entry"])
            exit_fee=position["qty"]*exit_price*fee
            net=gross-position["entry_fee"]-exit_fee+position["funding_net"]
            equity+=gross-exit_fee;fee_total+=exit_fee
            slippage_total+=position["qty"]*abs(raw-exit_price)
            if net<0: daily_loss+=abs(net)
            trades_out.append({"side":position["side"],"entry_timestamp":position["timestamp"],
                "exit_timestamp":row["timestamp"],"entry_price":position["entry"],
                "exit_price":exit_price,"quantity":position["qty"],"leverage":lev,
                "gross_pnl":gross,"fees":position["entry_fee"]+exit_fee,
                "funding_net":position["funding_net"],"net_pnl":net,"exit_reason":"end_of_data"})
            equity_curve[-1]=equity
        peak=max(peak,equity);max_dd=max(max_dd,peak-equity)
        return {"ok":True,"market":market.value,"initial_balance":balance,"final_equity":round(equity,8),
            "net_pnl":round(equity-balance,8),"total_return":round(equity/balance-1,8),
            "max_drawdown_quote":round(max_dd,8),"max_drawdown_pct":round(max_dd/max(peak,1e-12),8),
            "total_trades":len(trades_out),"trades":trades_out,"equity_curve":equity_curve,
            "fees_paid":round(fee_total,8),"slippage_cost":round(slippage_total,8),
            "funding_net":round(funding_net_total,8),"daily_limit_blocks":rejected_daily,
            "signal_model":"causal_9_21_sma_trend_atr_bracket_long_spot_long_short_futures",
            "fill_model":"next_open_gap_aware_stop_target_spread_and_fee",
            "same_bar_stop_target_policy":"stop_first_conservative","liquidation_model":"approximate_isolated_margin_ohlc_conservative_intrabar" if market==MarketType.FUTURES else "not_applicable_spot",
            "maintenance_margin_rate":mmr if market==MarketType.FUTURES else None,
            "liquidation_fee_rate":liq_fee_rate if market==MarketType.FUTURES else None,
            "liquidation_count":sum(1 for trade in trades_out if trade["exit_reason"]=="liquidation"),
            "cross_margin_mode":"not_simulated","no_lookahead":True,
            "execution":"simulation_only","environment":"demo","no_live_orders":True}
