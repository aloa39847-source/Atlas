"""Safe, single-cycle Demo/Shadow trading orchestrator.

This module never sends orders to an exchange. It creates internal Shadow
orders only after Demo Spot or USD-M Futures decisions pass safety checks.
"""
from __future__ import annotations
from typing import Any
from datetime import datetime, timezone
import hashlib
import math

from app.core.enums import MarketType
from app.services.core_service import core_service
from app.services.shadow_trading import ShadowTradingService
from app.models import db as dbmod
from app.models.entities import PositionRecord, SystemState
from app.services.audit import log_event
from sqlalchemy import select


class TradingOrchestrator:
    def __init__(self):
        self.shadow = ShadowTradingService()

    def _latch_emergency_stop(self, db, reason: str) -> None:
        """Persist an independent fail-closed stop after a critical subsystem failure."""
        state = db.get(SystemState, 1)
        if state is None:
            core_service.ensure_bootstrap(db)
            state = db.get(SystemState, 1)
        state.emergency_stop = True
        state.bot_run_state = "stopped"
        state.autonomous_enabled = False
        state.bot_stop_reason = reason
        state.bot_stopped_at = datetime.now(timezone.utc)
        db.add(state)
        market = "futures" if reason.startswith("futures_") else "spot"
        log_event(db, action="master.emergency_stop", severity="critical",
                  message="Emergency stop latched after a critical data or analysis subsystem failure.",
                  details={"reason": reason}, environment_mode="demo", market_type=market)
        db.commit()

    def _run_futures_cycle(
        self, db, *, symbol: str, execute_shadow: bool,
        max_data_age_seconds: int, require_active_worker: bool,
    ) -> dict[str, Any]:
        """One isolated Demo/Shadow USD-M Futures cycle; never contacts private APIs."""
        from app.core.config import settings

        selected_state = db.get(SystemState, 1)
        if (selected_state is None
                or selected_state.environment_mode != "demo"
                or getattr(selected_state, "account_target", "demo") != "demo"
                or selected_state.market_type != "futures"):
            return {"ok": False, "error": "demo_futures_selected_state_required", "executed": False}
        if bool(selected_state.emergency_stop):
            return {"ok": False, "error": "emergency_stop_active", "executed": False}

        try:
            batches = core_service.market_data.get_public_multi_timeframe(
                symbol, market_type="futures",
            )
            core_service._validate_public_market_batches(batches, expected_market="futures")
            latest_bar = batches[next(tf for tf in batches if tf.value == "5m")].df.rows[-1]
            if (isinstance(max_data_age_seconds, bool) or not isinstance(max_data_age_seconds, int)
                    or not 30 <= max_data_age_seconds <= 3600):
                raise ValueError("invalid_futures_max_data_age_seconds")
            candle_stamp = latest_bar.get("close_time") or latest_bar.get("timestamp")
            if isinstance(candle_stamp, bool) or candle_stamp is None:
                raise ValueError("missing_futures_m5_candle_timestamp")
            if isinstance(candle_stamp, (int, float)):
                candle_seconds = float(candle_stamp) / 1000.0
            elif isinstance(candle_stamp, str):
                candle_seconds = datetime.fromisoformat(candle_stamp.replace("Z", "+00:00")).timestamp()
            else:
                raise ValueError("invalid_futures_m5_candle_timestamp")
            candle_age = datetime.now(timezone.utc).timestamp() - candle_seconds
            if (not math.isfinite(candle_age) or candle_age < -60
                    or candle_age > max_data_age_seconds):
                self._latch_emergency_stop(db, "futures_m5_market_data_stale")
                return {"ok": False, "error": "stale_or_future_futures_market_data",
                        "emergency_stop_latched": True, "executed": False}
            mark = core_service.market_data.get_public_mark_price(symbol)
            raw_mark_price = mark.get("mark_price")
            raw_funding_rate = mark.get("last_funding_rate")
            raw_mark_time = mark.get("time")
            if any(isinstance(value, bool) or value is None for value in (
                raw_mark_price, raw_funding_rate, raw_mark_time,
            )):
                raise ValueError("invalid_boolean_or_missing_futures_mark_fields")
            mark_price = float(raw_mark_price)
            funding_rate = float(raw_funding_rate)
            mark_time = float(raw_mark_time)
            mark_age_ms = datetime.now(timezone.utc).timestamp() * 1000.0 - mark_time
            if (not all(math.isfinite(value) for value in (mark_price, funding_rate, mark_time, mark_age_ms))
                    or mark_price <= 0 or abs(funding_rate) > 0.05
                    or mark_age_ms < -60_000 or mark_age_ms > 120_000
                    or mark.get("source") != "binance_usdm_futures_public_read_only"):
                self._latch_emergency_stop(db, "futures_mark_price_invalid_or_stale")
                return {"ok": False, "error": "invalid_or_stale_futures_mark_price",
                        "emergency_stop_latched": True, "executed": False}
        except Exception as exc:
            self._latch_emergency_stop(db, "futures_public_data_failure")
            return {"ok": False, "error": "futures_data_feed_error", "detail": str(exc)[:240],
                    "emergency_stop_latched": True, "executed": False}

        open_positions = db.execute(select(PositionRecord).where(
            PositionRecord.environment_mode == "demo",
            PositionRecord.market_type == "futures",
            PositionRecord.status == "open",
            PositionRecord.shadow.is_(True),
        )).scalars().all()
        other_symbols = {position.symbol for position in open_positions if position.symbol != symbol}
        mark_prices = {symbol: mark_price}
        if other_symbols:
            try:
                mark_prices.update(core_service.market_data.get_public_last_prices(
                    other_symbols, market_type="futures",
                ))
            except Exception as exc:
                self._latch_emergency_stop(db, "futures_open_position_mark_failure")
                return {"ok": False, "error": "futures_open_position_marks_unavailable",
                        "detail": str(exc)[:240], "emergency_stop_latched": True, "executed": False}
            missing = sorted(other_symbols - set(mark_prices))
            if missing:
                self._latch_emergency_stop(db, "futures_open_position_mark_missing")
                return {"ok": False, "error": "futures_open_position_marks_missing",
                        "missing_symbols": missing, "emergency_stop_latched": True, "executed": False}

        # Settle every verified funding event since each open position's
        # cursor. Binance funding history is authoritative; payment uses the
        # current validated mark as an estimate because historical funding-time
        # mark prices are not fetched by this lightweight worker.
        funding_settlements = []
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        today_key = datetime.now(timezone.utc).date().isoformat()
        for position in open_positions:
            metadata = dict(position.metadata_json or {}) if isinstance(position.metadata_json, dict) else {}
            raw_cursor = metadata.get("funding_cursor_ms")
            if raw_cursor is None:
                metadata["funding_cursor_ms"] = min(int(mark_time), now_ms)
                metadata.setdefault("funding_daily_date", today_key)
                metadata.setdefault("funding_daily_pnl", 0.0)
                position.metadata_json = metadata
                db.add(position)
                continue
            if (isinstance(raw_cursor, bool)
                    or (isinstance(raw_cursor, float) and not raw_cursor.is_integer())):
                self._latch_emergency_stop(db, "futures_funding_cursor_invalid")
                return {"ok": False, "error": "futures_funding_cursor_invalid",
                        "emergency_stop_latched": True, "executed": False}
            try:
                cursor_ms = int(raw_cursor)
            except (TypeError, ValueError, OverflowError):
                cursor_ms = 0
            if cursor_ms <= 0 or cursor_ms > now_ms:
                self._latch_emergency_stop(db, "futures_funding_cursor_invalid")
                return {"ok": False, "error": "futures_funding_cursor_invalid",
                        "emergency_stop_latched": True, "executed": False}
            if now_ms <= cursor_ms:
                continue
            try:
                events = core_service.market_data.get_public_funding_rates(
                    position.symbol, start_time=cursor_ms + 1, end_time=now_ms,
                )
            except Exception as exc:
                self._latch_emergency_stop(db, "futures_funding_history_failure")
                return {"ok": False, "error": "futures_funding_history_unavailable",
                        "detail": str(exc)[:240], "emergency_stop_latched": True, "executed": False}
            position_mark = mark_prices.get(position.symbol)
            if position_mark is None:
                self._latch_emergency_stop(db, "futures_funding_mark_missing")
                return {"ok": False, "error": "futures_funding_mark_missing",
                        "emergency_stop_latched": True, "executed": False}
            direction = -1.0 if position.side == "short" else 1.0
            for event in events:
                try:
                    event_time = int(event["timestamp"])
                    rate = float(event["rate"])
                    mark_value = float(position_mark)
                    qty_value = float(position.quantity)
                except (KeyError, TypeError, ValueError, OverflowError):
                    self._latch_emergency_stop(db, "futures_funding_event_invalid")
                    return {"ok": False, "error": "futures_funding_event_invalid",
                            "emergency_stop_latched": True, "executed": False}
                if (event_time <= cursor_ms or event_time > now_ms
                        or not all(math.isfinite(value) for value in (rate, mark_value, qty_value))
                        or abs(rate) > 0.05 or mark_value <= 0 or qty_value <= 0):
                    self._latch_emergency_stop(db, "futures_funding_event_invalid")
                    return {"ok": False, "error": "futures_funding_event_invalid",
                            "emergency_stop_latched": True, "executed": False}
                payment = -direction * qty_value * mark_value * rate
                raw_realized = position.realized_pnl or 0.0
                raw_daily = metadata.get("funding_daily_pnl", 0.0)
                raw_total = metadata.get("funding_paid_total", 0.0)
                if any(isinstance(value, bool) for value in (raw_realized, raw_daily, raw_total)):
                    self._latch_emergency_stop(db, "futures_funding_accounting_invalid")
                    return {"ok": False, "error": "futures_funding_accounting_invalid",
                            "emergency_stop_latched": True, "executed": False}
                try:
                    realized_value = float(raw_realized)
                    daily_value = float(raw_daily) if metadata.get("funding_daily_date") == today_key else 0.0
                    total_value = float(raw_total)
                except (TypeError, ValueError, OverflowError):
                    self._latch_emergency_stop(db, "futures_funding_accounting_invalid")
                    return {"ok": False, "error": "futures_funding_accounting_invalid",
                            "emergency_stop_latched": True, "executed": False}
                if not all(math.isfinite(value) for value in (payment, realized_value, daily_value, total_value)):
                    self._latch_emergency_stop(db, "futures_funding_accounting_invalid")
                    return {"ok": False, "error": "futures_funding_accounting_invalid",
                            "emergency_stop_latched": True, "executed": False}
                position.realized_pnl = round(realized_value + payment, 8)
                if metadata.get("funding_daily_date") != today_key:
                    metadata["funding_daily_date"] = today_key
                metadata["funding_daily_pnl"] = round(daily_value + payment, 8)
                metadata["funding_paid_total"] = round(total_value + payment, 8)
                cursor_ms = event_time
                funding_settlements.append({
                    "position_id": position.id, "timestamp": event_time,
                    "funding_rate": rate, "estimated_payment": round(payment, 8),
                })
            metadata["funding_cursor_ms"] = cursor_ms
            position.metadata_json = metadata
            db.add(position)

        liquidation_exits = []
        for position in open_positions:
            metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
            price = mark_prices.get(position.symbol)
            if price is None:
                continue
            raw_position_values = (
                position.entry_avg_price, position.quantity,
                metadata.get("leverage", position.leverage or 1.0),
                metadata.get("entry_fees_remaining", 0.0),
                metadata.get("initial_margin_estimate", 0.0),
                metadata.get("funding_paid_total", 0.0),
            )
            if position.side not in {"long", "short"} or any(isinstance(value, bool) for value in raw_position_values):
                self._latch_emergency_stop(db, "futures_liquidation_state_invalid")
                return {"ok": False, "error": "futures_liquidation_state_invalid",
                        "emergency_stop_latched": True, "executed": False}
            try:
                entry = float(position.entry_avg_price or 0.0)
                quantity = float(position.quantity or 0.0)
                leverage = float(metadata.get("leverage", position.leverage or 1.0))
                entry_fees = float(metadata.get("entry_fees_remaining", entry * quantity * 0.001))
                initial_margin = float(metadata.get(
                    "initial_margin_estimate", entry * quantity / leverage,
                ))
                funding_paid_total = float(metadata.get("funding_paid_total", 0.0))
                mark = float(price)
            except (TypeError, ValueError, OverflowError, ZeroDivisionError):
                self._latch_emergency_stop(db, "futures_liquidation_state_invalid")
                return {"ok": False, "error": "futures_liquidation_state_invalid",
                        "emergency_stop_latched": True, "executed": False}
            if (not all(math.isfinite(value) for value in (
                    entry, quantity, leverage, entry_fees, initial_margin, funding_paid_total, mark))
                    or entry <= 0 or quantity <= 0 or leverage < 1
                    or entry_fees < 0 or initial_margin <= 0 or mark <= 0):
                self._latch_emergency_stop(db, "futures_liquidation_state_invalid")
                return {"ok": False, "error": "futures_liquidation_state_invalid",
                        "emergency_stop_latched": True, "executed": False}
            notional_at_entry = entry * quantity
            margin_after_costs = initial_margin - entry_fees - notional_at_entry * 0.005 + funding_paid_total
            if margin_after_costs <= 0:
                self._latch_emergency_stop(db, "futures_isolated_margin_buffer_exhausted")
                return {"ok": False, "error": "futures_isolated_margin_buffer_exhausted",
                        "emergency_stop_latched": True, "executed": False}
            maintenance_rate = 0.005
            is_short = position.side == "short"
            if is_short:
                liquidation_price = (entry + margin_after_costs / quantity) / (1.0 + maintenance_rate)
                liquidation_hit = mark >= liquidation_price
            else:
                liquidation_price = (entry - margin_after_costs / quantity) / (1.0 - maintenance_rate)
                liquidation_hit = liquidation_price > 0 and mark <= liquidation_price
            if not math.isfinite(liquidation_price):
                self._latch_emergency_stop(db, "futures_liquidation_price_invalid")
                return {"ok": False, "error": "futures_liquidation_price_invalid",
                        "emergency_stop_latched": True, "executed": False}
            if liquidation_hit:
                closed = self.shadow.close_shadow_position(
                    db, position_id=int(position.id), close_price=mark, fee_rate=0.005,
                )
                if not closed.get("ok"):
                    self._latch_emergency_stop(db, "futures_estimated_liquidation_close_failed")
                    return {"ok": False, "error": "futures_estimated_liquidation_close_failed",
                            "details": closed, "emergency_stop_latched": True, "executed": False}
                liquidation_exits.append({
                    "position_id": position.id, "reason": "estimated_liquidation",
                    "mark_price": mark, "estimated_liquidation_price": liquidation_price,
                    **closed,
                })
        if liquidation_exits:
            return {
                "ok": True, "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol, "market": "futures", "environment": "demo",
                "analysis_action": "wait", "executed": True, "execution_mode": "shadow_safe",
                "funding_settlements": funding_settlements,
                "execution_result": {
                    "message": "futures_estimated_liquidation_exit",
                    "closures": liquidation_exits,
                    "note": "Simplified isolated-margin estimate only; not Binance exchange liquidation calculation.",
                },
            }

        protective = self.shadow.evaluate_position_protection(
            db, env="demo", market="futures", prices=mark_prices,
        )
        if protective:
            return {
                "ok": all(item.get("ok") for item in protective),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol, "market": "futures", "environment": "demo",
                "analysis_action": "wait", "executed": all(item.get("ok") for item in protective),
                "funding_settlements": funding_settlements,
                "execution_mode": "shadow_safe" if all(item.get("ok") for item in protective) else "none",
                "execution_result": {"message": "futures_position_protection_exit", "closures": protective},
                "note": "Mark-price protection runs before analysis; no same-cycle re-entry.",
            }

        state = db.get(SystemState, 1)
        if state is None or bool(state.emergency_stop):
            return {"ok": False, "error": "emergency_stop_active_or_state_missing", "executed": False}
        raw_limits = getattr(state, "risk_limits", None)
        if raw_limits is not None and not isinstance(raw_limits, dict):
            return {"ok": False, "error": "invalid_demo_risk_configuration", "executed": False}
        limits = raw_limits or {}
        raw_balance = limits.get("demo_initial_balance", 150.0)
        raw_risk = limits.get("demo_risk_per_trade", 0.005)
        if isinstance(raw_balance, bool) or isinstance(raw_risk, bool):
            return {"ok": False, "error": "invalid_demo_risk_configuration", "executed": False}
        try:
            initial_equity = float(raw_balance)
            configured_risk = float(raw_risk)
            available_margin = float(self.shadow._cash_balance(db, env="demo", market="futures"))
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "invalid_demo_risk_configuration", "executed": False}
        if (not all(math.isfinite(value) for value in (initial_equity, configured_risk, available_margin))
                or initial_equity <= 0 or configured_risk <= 0 or configured_risk > 0.005
                or available_margin < 0):
            return {"ok": False, "error": "invalid_demo_risk_configuration", "executed": False}
        if available_margin <= 0:
            return {"ok": True, "executed": False, "execution_mode": "none",
                    "execution_result": {"message": "no_available_demo_futures_margin"}}

        risk_per_trade = configured_risk * min(1.0, available_margin / initial_equity)
        try:
            analysis = core_service.analyze(
                db, symbol=symbol, market_type="futures", environment_mode="demo",
                balance=initial_equity, max_leverage=float(settings.max_leverage_cap),
                risk_per_trade=risk_per_trade, data_source="binance_public",
                market_batches=batches,
            )
        except Exception as exc:
            self._latch_emergency_stop(db, "futures_analysis_engine_failure")
            return {"ok": False, "error": "futures_analysis_engine_error",
                    "detail": str(exc)[:240], "emergency_stop_latched": True, "executed": False}

        # Refresh control state after analysis, immediately before any entry/exit side effect.
        control_db = dbmod.SessionLocal()
        try:
            current = control_db.get(SystemState, 1)
            stopped = (current is None or bool(current.emergency_stop)
                       or (require_active_worker and current.bot_run_state not in {"running", "analyzing", "executing"}))
        finally:
            control_db.close()
        if stopped:
            return {"ok": False, "error": "emergency_stop_or_master_stop_requested", "executed": False}

        action = str(analysis.get("action", "wait")).lower()
        result: dict[str, Any] = {
            "ok": True, "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol, "market": "futures", "environment": "demo",
            "analysis_action": action, "decision": analysis, "executed": False,
            "execution_mode": "none", "mark_price": mark_price, "funding_rate": funding_rate,
            "risk_budget": {"initial_equity": initial_equity,
                            "available_margin": round(available_margin, 8),
                            "risk_per_trade_used": round(risk_per_trade, 8)},
            "funding_settlements": funding_settlements,
            "note": "Internal Demo/Shadow USD-M Futures simulation only; public data, no exchange orders.",
        }
        if action not in {"long", "short"}:
            result["execution_result"] = {"ok": True, "message": "wait_no_futures_trade"}
            return result
        if not execute_shadow:
            result["execution_result"] = {"ok": True, "message": "analysis_only_execution_disabled"}
            return result

        target_side = action
        opposite = "short" if target_side == "long" else "long"
        opposite_positions = [position for position in open_positions
                              if position.symbol == symbol and position.side == opposite]
        if opposite_positions:
            closures = []
            for position in opposite_positions:
                closures.append(self.shadow.close_shadow_position(
                    db, position_id=int(position.id), close_price=mark_price,
                ))
            result["executed"] = bool(closures) and all(item.get("ok") for item in closures)
            result["execution_mode"] = "shadow_safe" if result["executed"] else "none"
            result["execution_result"] = {
                "ok": result["executed"], "message": "opposite_futures_position_closed_no_reentry",
                "closures": closures,
            }
            return result

        risk = analysis.get("risk")
        if not isinstance(risk, dict):
            result["execution_result"] = {"ok": False, "message": "invalid_futures_risk_payload"}
            return result
        raw_entry = analysis.get("entry_price")
        raw_quantity = risk.get("quantity_estimate")
        raw_leverage = analysis.get("leverage", 1.0)
        if any(isinstance(value, bool) for value in (raw_entry, raw_quantity, raw_leverage)):
            result["execution_result"] = {"ok": False, "message": "boolean_futures_sizing_value_not_allowed"}
            return result
        try:
            entry_price = float(raw_entry or 0.0)
            quantity = float(raw_quantity or 0.0)
            leverage = float(raw_leverage or 1.0)
        except (TypeError, ValueError, OverflowError):
            result["execution_result"] = {"ok": False, "message": "invalid_futures_sizing_value"}
            return result
        if (risk.get("ok") is not True or not all(math.isfinite(value) for value in
                (entry_price, quantity, leverage)) or entry_price <= 0 or quantity <= 0
                or not 1.0 <= leverage <= float(settings.max_leverage_cap)):
            result["execution_result"] = {"ok": False, "message": "futures_risk_or_sizing_check_failed", "risk": risk}
            return result

        stop_loss = analysis.get("stop_loss")
        take_profit = analysis.get("take_profit")
        if isinstance(stop_loss, bool) or isinstance(take_profit, bool):
            result["execution_result"] = {"ok": False, "message": "boolean_futures_protection_value_not_allowed"}
            return result
        try:
            stop_loss = float(stop_loss)
            take_profit = float(take_profit)
            trade_cap = float(settings.max_futures_trade_notional)
        except (TypeError, ValueError, OverflowError):
            result["execution_result"] = {"ok": False, "message": "futures_protection_or_notional_configuration_missing"}
            return result
        if not math.isfinite(trade_cap) or trade_cap <= 0:
            result["execution_result"] = {"ok": False, "message": "invalid_futures_notional_cap"}
            return result

        existing_position = next((
            position for position in open_positions
            if position.symbol == symbol and position.side == target_side
        ), None)
        existing_qty = 0.0
        existing_entry = 0.0
        existing_margin = 0.0
        existing_entry_fees = 0.0
        existing_funding_paid = 0.0
        if existing_position:
            existing_metadata = existing_position.metadata_json if isinstance(existing_position.metadata_json, dict) else {}
            raw_existing_leverage = existing_metadata.get("leverage", existing_position.leverage or 1.0)
            raw_existing_fees = existing_metadata.get("entry_fees_remaining", 0.0)
            raw_existing_funding = existing_metadata.get("funding_paid_total", 0.0)
            if any(isinstance(value, bool) for value in (
                    raw_existing_leverage, raw_existing_fees, raw_existing_funding,
            )):
                self._latch_emergency_stop(db, "futures_existing_position_state_invalid")
                return {"ok": False, "error": "futures_existing_position_state_invalid",
                        "emergency_stop_latched": True, "executed": False}
            try:
                existing_leverage = float(raw_existing_leverage)
                existing_qty = float(existing_position.quantity)
                existing_entry = float(existing_position.entry_avg_price or 0.0)
                existing_entry_fees = float(raw_existing_fees)
                existing_funding_paid = float(raw_existing_funding)
            except (TypeError, ValueError, OverflowError):
                self._latch_emergency_stop(db, "futures_existing_position_state_invalid")
                return {"ok": False, "error": "futures_existing_position_state_invalid",
                        "emergency_stop_latched": True, "executed": False}
            if (not all(math.isfinite(value) for value in (
                    existing_leverage, existing_qty, existing_entry, existing_entry_fees, existing_funding_paid))
                    or existing_leverage < 1.0 or existing_qty <= 0
                    or existing_entry <= 0 or existing_entry_fees < 0):
                self._latch_emergency_stop(db, "futures_existing_position_state_invalid")
                return {"ok": False, "error": "futures_existing_position_state_invalid",
                        "emergency_stop_latched": True, "executed": False}
            if existing_leverage != leverage:
                result["execution_result"] = {"ok": False, "message": "existing_futures_position_leverage_mismatch"}
                return result
            existing_margin = existing_entry * existing_qty / existing_leverage

        # The 800 USDT limit is gross notional for the entire same-side
        # position, not just the next order. Re-size against current mark.
        remaining_position_capacity = trade_cap - existing_qty * mark_price
        if remaining_position_capacity <= 0:
            result["execution_result"] = {"ok": False, "message": "futures_position_notional_cap_reached"}
            return result
        quantity = min(quantity, remaining_position_capacity / mark_price)
        projected_qty = existing_qty + quantity
        projected_notional = projected_qty * mark_price
        if (quantity <= 0 or projected_notional > trade_cap + 1e-8
                or not math.isfinite(projected_notional)):
            result["execution_result"] = {"ok": False, "message": "futures_position_notional_cap_failed"}
            return result
        projected_entry = (
            (existing_entry * existing_qty + mark_price * quantity) / projected_qty
            if projected_qty > 0 else mark_price
        )
        # Conservative isolated-margin liquidation estimate. This is an
        # internal safety screen, not Binance's tiered exchange calculation.
        maintenance_rate = 0.005
        new_entry_fee = quantity * mark_price * 0.001
        liquidation_fee_estimate = projected_notional * 0.005
        isolated_margin_estimate = existing_margin + (quantity * mark_price / leverage)
        total_entry_fees_estimate = existing_entry_fees + new_entry_fee
        margin_after_costs = isolated_margin_estimate - total_entry_fees_estimate - liquidation_fee_estimate + existing_funding_paid
        if not math.isfinite(margin_after_costs) or margin_after_costs <= 0:
            result["execution_result"] = {"ok": False, "message": "futures_isolated_margin_buffer_insufficient"}
            return result
        if target_side == "long":
            valid_protection = (
                math.isfinite(stop_loss) and math.isfinite(take_profit)
                and stop_loss < projected_entry < take_profit
                and stop_loss < mark_price < take_profit
            )
            estimated_liquidation_price = (
                projected_entry - margin_after_costs / projected_qty
            ) / (1.0 - maintenance_rate)
            stop_precedes_liquidation = stop_loss > estimated_liquidation_price
        else:
            valid_protection = (
                math.isfinite(stop_loss) and math.isfinite(take_profit)
                and take_profit < projected_entry < stop_loss
                and take_profit < mark_price < stop_loss
            )
            estimated_liquidation_price = (
                projected_entry + margin_after_costs / projected_qty
            ) / (1.0 + maintenance_rate)
            stop_precedes_liquidation = stop_loss < estimated_liquidation_price
        if not valid_protection:
            result["execution_result"] = {"ok": False, "message": "invalid_futures_protection_for_projected_position"}
            return result
        if (not math.isfinite(estimated_liquidation_price)
                or estimated_liquidation_price <= 0 or not stop_precedes_liquidation):
            result["execution_result"] = {
                "ok": False, "message": "futures_stop_not_safely_ahead_of_estimated_liquidation",
                "estimated_liquidation_price": estimated_liquidation_price,
            }
            return result
        candle_identity = latest_bar.get("close_time") or latest_bar.get("timestamp") or "unknown-candle"
        digest = hashlib.sha256(
            f"{symbol}|{candle_identity}|futures|{target_side}".encode("utf-8")
        ).hexdigest()[:24]
        idem = f"shadow-cycle-{symbol.replace('/', '')}-{digest}"
        side = "buy" if target_side == "long" else "sell"
        shadow_res = self.shadow.create_shadow_order(
            db, env="demo", market="futures", symbol=symbol, side=side, order_type="market",
            quantity=quantity, price=mark_price, reference_price=mark_price,
            idempotency_key=idem, simulate="fill", mark_price=mark_price, leverage=leverage,
        )
        executed = bool(shadow_res.ok and shadow_res.order and shadow_res.order.filled_quantity > 0)
        result["executed"] = executed
        result["execution_mode"] = "shadow_safe" if executed else "none"
        protection = None
        if executed and shadow_res.position:
            metadata = dict(shadow_res.position.metadata_json or {})
            funding_now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            current_cursor = metadata.get("funding_cursor_ms")
            if current_cursor is None:
                metadata["funding_cursor_ms"] = min(int(mark_time), funding_now_ms)
            else:
                metadata["funding_cursor_ms"] = max(int(current_cursor), min(int(mark_time), funding_now_ms))
            if metadata.get("funding_daily_date") != today_key:
                metadata["funding_daily_date"] = today_key
                metadata["funding_daily_pnl"] = 0.0
            metadata.setdefault("funding_daily_pnl", 0.0)
            metadata.setdefault("funding_paid_total", 0.0)
            metadata.update({
                "stop_loss": stop_loss, "take_profit": take_profit,
                "protection_source": "decision_engine",
                "protection_updated_at": datetime.now(timezone.utc).isoformat(),
                "mark_price_source": "binance_usdm_futures_public_read_only",
                "funding_rate_at_entry": funding_rate,
                "margin_mode": "isolated",
                "initial_margin_estimate": round(isolated_margin_estimate, 8),
                "maintenance_margin_rate_estimate": maintenance_rate,
                "estimated_liquidation_price": round(estimated_liquidation_price, 8),
                "liquidation_estimate_model": "simplified_internal_not_binance_exchange_calculated",
            })
            shadow_res.position.metadata_json = metadata
            db.add(shadow_res.position)
            protection = {"stop_loss": stop_loss, "take_profit": take_profit}
        result["protection"] = protection
        result["execution_result"] = {
            "ok": shadow_res.ok, "message": shadow_res.message,
            "order_id": shadow_res.order.id if shadow_res.order else None,
            "status": shadow_res.order.status if shadow_res.order else None,
            "filled_quantity": shadow_res.order.filled_quantity if shadow_res.order else 0.0,
            "details": shadow_res.details,
        }
        return result

    def run_cycle(
        self,
        db,
        *,
        symbol: str,
        market: str = "spot",
        environment: str = "demo",
        execute_shadow: bool = True,
        execute_testnet: bool | None = None,
        max_data_age_seconds: int = 300,
        require_active_worker: bool = False,
    ) -> dict[str, Any]:
        # Backward compatibility; this flag never means external Binance execution.
        if execute_testnet is not None:
            execute_shadow = bool(execute_testnet)
        if environment != "demo":
            return {"ok": False, "error": "demo_environment_required", "executed": False}
        if market == MarketType.FUTURES.value:
            return self._run_futures_cycle(
                db, symbol=symbol, execute_shadow=execute_shadow,
                max_data_age_seconds=max_data_age_seconds,
                require_active_worker=require_active_worker,
            )
        if market != MarketType.SPOT.value:
            return {"ok": False, "error": "unsupported_market_execution_policy", "executed": False}

        latest_bar = None
        try:
            batches = core_service.market_data.get_public_multi_timeframe(symbol)
            for tf, batch in batches.items():
                if not batch.df or len(batch.df) < 30:
                    return {"ok": False, "error": "insufficient_market_data", "timeframe": tf.value, "executed": False}
            latest_bar = batches[next(tf for tf in batches if tf.value == "5m")].df.rows[-1]
            latest_ts = latest_bar.get("close_time") or latest_bar.get("timestamp")
            if isinstance(latest_ts, (int, float)):
                age_seconds = datetime.now(timezone.utc).timestamp() - (float(latest_ts) / 1000.0)
                if age_seconds > max_data_age_seconds or age_seconds < -60:
                    return {"ok": False, "error": "stale_or_future_market_data", "age_seconds": round(age_seconds, 2), "executed": False}
        except Exception as e:
            self._latch_emergency_stop(db, "binance_public_data_failure")
            return {"ok": False, "error": "data_feed_error", "detail": str(e), "emergency_stop_latched": True, "executed": False}

        # Mark existing positions from the latest verified public candle before
        # risk checks, so the daily-loss guard sees floating losses as well.
        try:
            mark_price = float((latest_bar or {}).get("close") or 0.0)
        except (TypeError, ValueError):
            mark_price = 0.0
        if mark_price <= 0:
            return {"ok": False, "error": "invalid_market_mark_price", "executed": False}
        # Refresh every open position from recent public tickers before
        # applying portfolio-wide daily-loss and protective-exit checks.
        open_positions = db.execute(select(PositionRecord).where(
            PositionRecord.environment_mode == environment,
            PositionRecord.market_type == market,
            PositionRecord.status == "open",
            PositionRecord.shadow.is_(True),
        )).scalars().all()
        other_symbols = {p.symbol for p in open_positions if p.symbol != symbol}
        mark_prices = {symbol: mark_price}
        if other_symbols:
            try:
                mark_prices.update(core_service.market_data.get_public_last_prices(other_symbols))
            except Exception as e:
                self._latch_emergency_stop(db, "open_position_market_data_failure")
                return {"ok": False, "error": "open_position_marks_unavailable",
                        "detail": str(e), "missing_symbols": sorted(other_symbols), "emergency_stop_latched": True, "executed": False}
            missing_marks = sorted(other_symbols - set(mark_prices))
            if missing_marks:
                self._latch_emergency_stop(db, "open_position_market_data_missing")
                return {"ok": False, "error": "open_position_marks_stale_or_missing",
                        "missing_symbols": missing_marks, "emergency_stop_latched": True, "executed": False}
        protection_exits = self.shadow.evaluate_position_protection(
            db, env=environment, market=market, prices=mark_prices,
        )
        if protection_exits:
            return {
                "ok": all(item.get("ok") for item in protection_exits),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol, "market": market, "environment": environment,
                "analysis_action": "wait", "executed": all(item.get("ok") for item in protection_exits),
                "execution_mode": "shadow_safe",
                "execution_result": {"message": "position_protection_exit", "closures": protection_exits},
                "note": "Protective exit evaluated before new entries; no same-cycle re-entry is allowed.",
            }

        state = db.get(SystemState, 1)
        if state is not None and bool(state.emergency_stop):
            return {"ok": False, "error": "emergency_stop_active", "emergency_stop_latched": True, "executed": False}
        raw_limits = getattr(state, "risk_limits", None) if state else None
        if raw_limits is not None and not isinstance(raw_limits, dict):
            return {"ok": False, "error": "invalid_demo_risk_configuration", "executed": False}
        limits = raw_limits or {}
        raw_balance = limits.get("demo_initial_balance", 150.0)
        raw_risk = limits.get("demo_risk_per_trade", 0.005)
        if isinstance(raw_balance, bool) or isinstance(raw_risk, bool):
            return {"ok": False, "error": "invalid_demo_risk_configuration", "executed": False}
        try:
            demo_balance = float(raw_balance)
            configured_risk_per_trade = float(raw_risk)
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "invalid_demo_risk_configuration", "executed": False}
        if (not math.isfinite(demo_balance) or not math.isfinite(configured_risk_per_trade)
                or demo_balance <= 0 or configured_risk_per_trade <= 0
                or configured_risk_per_trade > 0.02):
            return {"ok": False, "error": "invalid_demo_risk_configuration", "executed": False}
        # Size new Spot risk from remaining quote cash, not the original
        # starting balance. Portfolio exposure still uses the configured
        # initial account balance inside the risk engine.
        available_cash = self.shadow.get_available_cash(db, env=environment, market=market)
        if available_cash <= 0:
            return {"ok": True, "timestamp": datetime.now(timezone.utc).isoformat(),
                    "symbol": symbol, "market": market, "environment": environment,
                    "analysis_action": "wait", "executed": False, "execution_mode": "none",
                    "execution_result": {"ok": True, "message": "no_available_demo_quote_balance"},
                    "risk_budget": {"available_quote_cash": available_cash, "initial_balance": demo_balance}}
        allocation_ratio = min(1.0, available_cash / demo_balance)
        risk_per_trade = configured_risk_per_trade * allocation_ratio
        try:
            analysis = core_service.analyze(
                db, symbol=symbol, market_type=market, environment_mode=environment,
                balance=demo_balance, max_leverage=1.0, risk_per_trade=risk_per_trade,
                data_source="binance_public", market_batches=batches,
            )
        except Exception as e:
            self._latch_emergency_stop(db, "analysis_engine_failure")
            return {"ok": False, "error": "analysis_engine_error", "detail": str(e), "emergency_stop_latched": True, "executed": False}

        # Re-check shared stop controls after analysis and immediately before
        # any Shadow side effect. A Master Stop/Emergency Stop arriving during
        # a slow market-data or analysis call must still block this cycle.
        current_state = db.get(SystemState, 1)
        if current_state is not None and bool(current_state.emergency_stop):
            return {"ok": False, "error": "emergency_stop_active", "executed": False}
        if require_active_worker and (
            current_state is None or current_state.bot_run_state not in {"running", "analyzing", "executing"}
        ):
            return {"ok": False, "error": "master_stop_requested", "executed": False}

        action = str(analysis.get("action", "wait")).lower()
        result: dict[str, Any] = {
            "ok": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "market": market,
            "environment": environment,
            "analysis_action": action,
            "decision": analysis,
            "executed": False,
            "execution_mode": "none",
            "data_source": analysis.get("data_source", "binance_public"),
            "risk_budget": {"initial_balance": demo_balance, "available_quote_cash": round(available_cash, 8),
                            "allocation_ratio": round(allocation_ratio, 8), "risk_per_trade_used": round(risk_per_trade, 8)},
            "note": "Single-cycle internal Shadow simulation only; market candles are read-only Binance Public data. No Binance order is sent.",
        }

        if action == "wait":
            result["execution_result"] = {"ok": True, "message": "wait_no_trade"}
            return result
        if action == "sell":
            if not execute_shadow:
                result["execution_result"] = {"ok": True, "message": "analysis_only_execution_disabled"}
                return result
            portfolio = self.shadow.get_portfolio(db, env="demo", market="spot")
            open_positions = [p for p in portfolio["positions"] if p["symbol"] == symbol and p["side"] == "long_spot"]
            if not open_positions:
                result["execution_result"] = {"ok": True, "message": "no_open_spot_position"}
                return result
            close_price = float(analysis.get("entry_price") or (latest_bar or {}).get("close") or 0.0)
            if close_price <= 0:
                result["execution_result"] = {"ok": False, "message": "invalid_sell_reference_price"}
                return result
            closed = []
            for position in open_positions:
                close_result = self.shadow.close_shadow_position(
                    db, position_id=int(position["id"]), close_price=close_price,
                )
                closed.append({"position_id": position["id"], **close_result})
            result["executed"] = all(item.get("ok") for item in closed)
            result["execution_mode"] = "shadow_safe" if result["executed"] else "none"
            result["execution_result"] = {"ok": result["executed"], "message": "spot_positions_closed" if result["executed"] else "spot_close_failed", "closures": closed}
            return result
        if action != "buy":
            result["execution_result"] = {"ok": False, "message": "unsupported_spot_action"}
            return result
        if not execute_shadow:
            result["execution_result"] = {"ok": True, "message": "analysis_only_execution_disabled"}
            return result

        risk = analysis.get("risk") or {}
        price = float(analysis.get("entry_price") or 0.0)
        quantity = float(risk.get("quantity_estimate") or 0.0)
        if not risk.get("ok") or price <= 0 or quantity <= 0:
            result["execution_result"] = {"ok": False, "message": "risk_or_sizing_check_failed", "risk": risk}
            return result

        candle_identity = (latest_bar or {}).get("close_time") or (latest_bar or {}).get("timestamp") or "unknown-candle"
        idem_digest = hashlib.sha256(f"{symbol}|{candle_identity}|spot|buy".encode("utf-8")).hexdigest()[:24]
        idem = f"shadow-cycle-{symbol.replace('/', '')}-{idem_digest}"
        shadow_res = self.shadow.create_shadow_order(
            db, env="demo", market="spot", symbol=symbol, side="buy", order_type="limit",
            quantity=quantity, price=price, reference_price=price, idempotency_key=idem,
            simulate="fill", mark_price=price,
        )
        result["executed"] = bool(shadow_res.ok and shadow_res.order and shadow_res.order.filled_quantity > 0)
        result["execution_mode"] = "shadow_safe" if result["executed"] else "none"
        protection = None
        if result["executed"] and shadow_res.position:
            metadata = dict(shadow_res.position.metadata_json or {})
            if analysis.get("stop_loss") is not None:
                metadata["stop_loss"] = float(analysis["stop_loss"])
            if analysis.get("take_profit") is not None:
                metadata["take_profit"] = float(analysis["take_profit"])
            metadata["protection_source"] = "decision_engine"
            metadata["protection_updated_at"] = datetime.now(timezone.utc).isoformat()
            shadow_res.position.metadata_json = metadata
            db.add(shadow_res.position)
            protection = {"stop_loss": metadata.get("stop_loss"), "take_profit": metadata.get("take_profit")}
        result["protection"] = protection
        result["execution_result"] = {
            "ok": shadow_res.ok, "message": shadow_res.message,
            "order_id": shadow_res.order.id if shadow_res.order else None,
            "status": shadow_res.order.status if shadow_res.order else None,
            "filled_quantity": shadow_res.order.filled_quantity if shadow_res.order else 0.0,
            "details": shadow_res.details,
        }
        return result
