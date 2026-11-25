"""TITAN's single execution boundary for Binance Testnet and Live.

This module is intentionally not started at application boot. A supervised
worker must call it only after the user starts the selected account target.
It never retries an order whose exchange outcome is uncertain.
"""
from __future__ import annotations

import math
import hashlib
import os
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.integrations.binance_adapter import BinanceConfig, BinanceTestnetAdapter, BinanceLiveAdapter
from app.models.entities import OrderRecord, SystemState
from app.services.credentials import load_binance_credentials, load_testnet_credentials
from app.services.audit import log_event


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


class BinanceExecutionService:
    """Environment-routed execution with durable idempotency and fail-closed state."""

    def adapter_for(self, state: SystemState, *, live_worker_ready: bool = False) -> BinanceTestnetAdapter:
        target = str(getattr(state, "account_target", "demo") or "demo").lower()
        market = str(getattr(state, "market_type", "spot") or "spot").lower()
        if market not in {"spot", "futures"}:
            raise ValueError("unsupported_binance_market")
        if target == "testnet":
            creds = load_testnet_credentials(market)
            configured = bool(creds.get("configured"))
            prefix = f"BINANCE_TESTNET_{market.upper()}"
            market_key, market_secret = os.getenv(f"{prefix}_API_KEY"), os.getenv(f"{prefix}_API_SECRET")
            generic_allowed = os.getenv("BINANCE_TESTNET_MARKET", "spot").strip().lower() == market
            env_key = f"env:{prefix}_API_KEY" if market_key else (
                "env:BINANCE_TESTNET_API_KEY" if generic_allowed and os.getenv("BINANCE_TESTNET_API_KEY") else None
            )
            env_secret = f"env:{prefix}_API_SECRET" if market_secret else (
                "env:BINANCE_TESTNET_API_SECRET" if generic_allowed and os.getenv("BINANCE_TESTNET_API_SECRET") else None
            )
            key_ref = f"stored:testnet:{market}:api_key" if configured else env_key
            secret_ref = f"stored:testnet:{market}:api_secret" if configured else env_secret
            return BinanceTestnetAdapter(BinanceConfig(
                enabled=bool(configured or (key_ref and secret_ref)),
                environment="testnet", market=market,
                api_key_ref=key_ref, api_secret_ref=secret_ref,
            ))
        if target == "live":
            creds = load_binance_credentials("live", market)
            configured = bool(creds.get("configured"))
            prefix = f"BINANCE_LIVE_{market.upper()}"
            market_key, market_secret = os.getenv(f"{prefix}_API_KEY"), os.getenv(f"{prefix}_API_SECRET")
            generic_allowed = os.getenv("BINANCE_LIVE_MARKET", "spot").strip().lower() == market
            env_key = f"env:{prefix}_API_KEY" if market_key else (
                "env:BINANCE_LIVE_API_KEY" if generic_allowed and os.getenv("BINANCE_LIVE_API_KEY") else None
            )
            env_secret = f"env:{prefix}_API_SECRET" if market_secret else (
                "env:BINANCE_LIVE_API_SECRET" if generic_allowed and os.getenv("BINANCE_LIVE_API_SECRET") else None
            )
            key_ref = f"stored:live:{market}:api_key" if configured else env_key
            secret_ref = f"stored:live:{market}:api_secret" if configured else env_secret
            return BinanceLiveAdapter(BinanceConfig(
                enabled=bool(configured or (key_ref and secret_ref)),
                environment="live", market=market,
                api_key_ref=key_ref, api_secret_ref=secret_ref,
                live_confirmed_by_user=bool(getattr(state, "live_confirmed_by_user", False)),
                live_geo_eligible_confirmed=_flag("TITAN_LIVE_GEO_ELIGIBLE_CONFIRMED"),
                live_kyc_confirmed=_flag("TITAN_LIVE_KYC_CONFIRMED"),
                live_rate_limit_control_enabled=_flag("TITAN_LIVE_RATE_LIMIT_CONTROL_ENABLED"),
                live_worker_ready=bool(live_worker_ready),
            ))
        raise ValueError("binance_execution_requires_testnet_or_live_account_target")

    def preflight(
        self, db, *, live_worker_ready: bool = False,
        allow_emergency_exit: bool = False,
    ) -> dict[str, Any]:
        state = db.get(SystemState, 1)
        if state is None:
            return {"ok": False, "ready": False, "error": "system_state_missing"}
        if bool(state.emergency_stop) and not allow_emergency_exit:
            return {"ok": False, "ready": False, "error": "emergency_stop_active"}
        target = str(getattr(state, "account_target", "demo") or "demo").lower()
        record = None
        try:
            adapter = self.adapter_for(state, live_worker_ready=live_worker_ready)
            status = adapter.status()
        except Exception as exc:
            return {"ok": False, "ready": False, "error": "binance_adapter_unavailable", "detail": str(exc)[:200]}
        if target == "testnet":
            ready = bool(status.get("configured") and status.get("connected") and status.get("execution_ready"))
        elif target == "live":
            ready = bool(status.get("execution_ready"))
        else:
            ready = False
        return {
            "ok": ready,
            "ready": ready,
            "account_target": target,
            "market": state.market_type,
            "status": status,
            "orders_submitted": False,
        }

    def cancel_order(self, db, *, local_order_id: int) -> dict[str, Any]:
        """Cancel a tracked exchange order without creating a replacement."""
        state = db.get(SystemState, 1)
        if state is None:
            return {"ok": False, "sent_to_exchange": False, "error": "system_state_missing"}
        target = str(getattr(state, "account_target", "demo") or "demo").lower()
        market = str(state.market_type or "spot")
        if target not in {"testnet", "live"}:
            return {"ok": False, "sent_to_exchange": False, "error": "external_binance_account_required"}
        order = db.get(OrderRecord, int(local_order_id))
        if order is None or order.shadow:
            return {"ok": False, "sent_to_exchange": False, "error": "tracked_exchange_order_not_found"}
        if order.environment_mode != target or order.market_type != market:
            return {"ok": False, "sent_to_exchange": False, "error": "order_environment_mismatch"}
        if order.status not in {"pending", "new", "submitted", "partially_filled", "unknown"}:
            return {"ok": True, "sent_to_exchange": False, "already_terminal": True, "status": order.status}
        if not order.client_order_id and not order.exchange_order_id:
            return {"ok": False, "sent_to_exchange": False, "error": "exchange_order_identifier_missing"}
        try:
            adapter = self.adapter_for(state, live_worker_ready=(target == "live"))
            result = adapter.cancel_order(
                order.symbol, order_id=order.exchange_order_id,
                client_order_id=order.client_order_id,
            )
            response = result.get("response") if isinstance(result, dict) else None
            if result.get("sent_to_exchange") and isinstance(response, dict):
                raw_status = str(response.get("status") or "").lower()
                status_map = {"canceled": "cancelled", "cancelled": "cancelled",
                              "filled": "filled", "partially_filled": "partially_filled",
                              "new": "new", "expired": "expired", "rejected": "rejected"}
                new_status = status_map.get(raw_status)
                try:
                    executed_quantity = float(response.get("executedQty", order.filled_quantity or 0) or 0)
                except (TypeError, ValueError, OverflowError):
                    executed_quantity = float("nan")
                if new_status is None or not math.isfinite(executed_quantity) or executed_quantity < 0:
                    order.status = "unknown"
                    order.reason = "Cancel acknowledged with invalid or unrecognized exchange status/fill data."
                else:
                    # Keep any executed fill in the active reconciliation queue
                    # until the worker materializes the corresponding position.
                    order.status = "partially_filled" if executed_quantity > 0 else new_status
                    order.filled_quantity = executed_quantity
                    order.exchange_order_id = str(response.get("orderId") or order.exchange_order_id or "")
                    order.reason = ("Cancel confirmed; partial/full fill awaits local position synchronization."
                                    if executed_quantity > 0 else "Exchange cancellation confirmed.")
                db.add(order)
                if order.status == "unknown":
                    state.emergency_stop = True
                    state.bot_run_state = "stopped"
                    state.autonomous_enabled = False
                    state.bot_stop_reason = "binance_cancel_status_unknown"
                    db.add(state)
                db.commit()
                return {"ok": order.status != "unknown", "sent_to_exchange": True,
                        "order_id": order.id, "status": order.status, "response": response}
            order.status = "unknown"
            order.reason = "Cancel request outcome uncertain; no automatic replacement or retry."
            state.emergency_stop = True
            state.bot_run_state = "stopped"
            state.autonomous_enabled = False
            state.bot_stop_reason = "binance_cancel_outcome_uncertain"
            db.add(order)
            db.add(state)
            db.commit()
            return {"ok": False, "sent_to_exchange": bool(result.get("sent_to_exchange")),
                    "error": result.get("error") or "cancel_outcome_uncertain",
                    "order_id": order.id, "status": order.status}
        except Exception as exc:
            db.rollback()
            return {"ok": False, "sent_to_exchange": False, "error": "cancel_failed",
                    "detail": str(exc)[:200]}

    def execute_order(
        self, db, *, symbol: str, side: str, order_type: str, quantity: float,
        price: float | None = None, leverage: float = 1.0,
        reduce_only: bool = False, idempotency_key: str,
        live_worker_ready: bool = False, order_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = db.get(SystemState, 1)
        if state is None:
            return {"ok": False, "sent_to_exchange": False, "error": "system_state_missing"}
        target = str(getattr(state, "account_target", "demo") or "demo").lower()
        market = str(state.market_type or "spot")
        environment = target
        if target not in {"testnet", "live"}:
            return {"ok": False, "sent_to_exchange": False, "error": "external_execution_requires_binance_account"}
        if target == "live" and not bool(getattr(state, "live_confirmed_by_user", False)):
            return {"ok": False, "sent_to_exchange": False,
                    "error": "live_requires_explicit_user_confirmation"}
        if not isinstance(idempotency_key, str) or len(idempotency_key.strip()) < 8:
            return {"ok": False, "sent_to_exchange": False, "error": "valid_idempotency_key_required"}
        # Idempotency lookup must precede worker/emergency-stop gates: a replay
        # of an already-recorded key must return the durable prior outcome,
        # never be mistaken for a new request or lose its duplicate response.
        key = idempotency_key.strip()
        existing = db.execute(select(OrderRecord).where(OrderRecord.idempotency_key == key)).scalar_one_or_none()
        if existing is not None:
            return {
                "ok": existing.status in {"submitted", "partially_filled", "filled"},
                "sent_to_exchange": False,
                "duplicate": True,
                "error": "idempotency_key_already_recorded_no_resubmission",
                "order_id": existing.id,
                "status": existing.status,
            }

        is_position_exit = bool(reduce_only) or (order_metadata or {}).get("intent") == "close"
        if state.bot_run_state not in {"running", "analyzing", "executing"} and not is_position_exit:
            return {"ok": False, "sent_to_exchange": False, "error": "master_worker_not_running"}
        if state.emergency_stop and not is_position_exit:
            return {"ok": False, "sent_to_exchange": False, "error": "emergency_stop_active"}
        try:
            qty = float(quantity)
            lev = float(leverage)
            px = float(price) if price is not None else None
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "sent_to_exchange": False, "error": "invalid_order_numeric_input"}
        if (not math.isfinite(qty) or qty <= 0 or not math.isfinite(lev) or lev < 1
                or (px is not None and (not math.isfinite(px) or px <= 0))):
            return {"ok": False, "sent_to_exchange": False, "error": "invalid_order_numeric_input"}

        if order_metadata is not None and not isinstance(order_metadata, dict):
            return {"ok": False, "sent_to_exchange": False, "error": "order_metadata_must_be_object"}

        if bool((state.risk_limits or {}).get("trading_paused", False)) and not is_position_exit:
            return {"ok": False, "sent_to_exchange": False, "error": "new_trade_entries_paused"}

        # Enforce the configured notional cap at the final execution boundary.
        # Position-reducing exits are exempt so safety controls never block a close.
        if not is_position_exit:
            reference_price = px
            if reference_price is None:
                try:
                    reference_price = float((order_metadata or {}).get("reference_price"))
                except (TypeError, ValueError, OverflowError):
                    reference_price = None
            if (market not in {"spot", "futures"} or reference_price is None
                    or not math.isfinite(float(reference_price)) or float(reference_price) <= 0):
                return {"ok": False, "sent_to_exchange": False, "error": "entry_requires_valid_reference_price"}
            estimated_notional = qty * float(reference_price) * 1.03
            notional_cap = settings.max_spot_trade_notional if market == "spot" else settings.max_futures_trade_notional
            if not math.isfinite(estimated_notional) or estimated_notional > float(notional_cap) + 1e-9:
                return {"ok": False, "sent_to_exchange": False,
                        "error": "per_trade_notional_cap_exceeded",
                        "estimated_notional_usdt": estimated_notional, "max_notional_usdt": float(notional_cap)}
        try:
            adapter = self.adapter_for(state, live_worker_ready=live_worker_ready)
            preflight = self.preflight(
                db, live_worker_ready=live_worker_ready,
                allow_emergency_exit=is_position_exit,
            )
            if not preflight.get("ready"):
                return {"ok": False, "sent_to_exchange": False, "error": "binance_preflight_not_ready", "preflight": preflight}

            pending = db.execute(select(OrderRecord).where(
                OrderRecord.environment_mode == environment,
                OrderRecord.market_type == market,
                OrderRecord.shadow.is_(False),
                OrderRecord.status.in_(["pending", "new", "submitted", "partially_filled", "unknown"]),
            )).scalars().all()
            reconciliation = adapter.reconcile(local_open_orders=[
                {
                    "id": row.exchange_order_id or row.id,
                    "orderId": row.exchange_order_id,
                    "clientOrderId": row.client_order_id,
                }
                for row in pending
            ])
            if not reconciliation.get("ok"):
                state.emergency_stop = True
                state.bot_run_state = "stopped"
                state.autonomous_enabled = False
                state.bot_stop_reason = "binance_order_reconciliation_mismatch"
                log_event(
                    db, action="master.emergency_stop", severity="critical",
                    message="Binance/local open-order reconciliation mismatch; new orders halted.",
                    details={"reconciliation": reconciliation},
                    environment_mode=environment, market_type=market,
                )
                db.add(state)
                db.commit()
                return {"ok": False, "sent_to_exchange": False,
                        "error": "exchange_reconciliation_required_emergency_stop",
                        "reconciliation": reconciliation}

            exchange_client_id = "titan" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:28]
            record = OrderRecord(
                environment_mode=environment, market_type=market, symbol=symbol.upper(),
                side=side.lower(), order_type=order_type.lower(), quantity=qty,
                status="pending", shadow=False, idempotency_key=key,
                client_order_id=exchange_client_id, reason="TITAN execution request; durable idempotency record created before exchange submission.",
                metadata_json={
                    **(order_metadata or {}),
                    "account_target": target, "leverage": lev,
                    "reduce_only": bool(reduce_only),
                },
            )
            db.add(record)
            db.commit()
            db.refresh(record)

            result = adapter.place_order(
                symbol=symbol, side=side, order_type=order_type, quantity=qty,
                price=px, leverage=lev, reduce_only=bool(reduce_only),
                idempotency_key=key,
            )
            if result.get("sent_to_exchange"):
                exchange = result.get("response") if isinstance(result.get("response"), dict) else {}
                exchange_status = str(exchange.get("status", "unknown")).lower()
                status_map = {
                    "new": "new", "partially_filled": "partially_filled",
                    "filled": "filled", "canceled": "cancelled",
                    "cancelled": "cancelled", "expired": "expired",
                    "rejected": "rejected",
                }
                record.status = status_map.get(exchange_status, "unknown")
                record.exchange_order_id = str(exchange.get("orderId")) if exchange.get("orderId") is not None else None
                record.client_order_id = str(exchange.get("clientOrderId") or exchange_client_id)
                record.filled_quantity = float(exchange.get("executedQty", 0) or 0)
                average_price = float(exchange.get("avgPrice", 0) or 0)
                if average_price <= 0 and record.filled_quantity > 0:
                    quote_total = float(exchange.get("cummulativeQuoteQty", 0) or 0)
                    if quote_total > 0:
                        average_price = quote_total / record.filled_quantity
                record.avg_fill_price = average_price or None
                fee_accounting = result.get("fee_accounting")
                if record.filled_quantity > 0 and isinstance(fee_accounting, dict):
                    fee_meta = dict(record.metadata_json or {})
                    if fee_accounting.get("ok") is True:
                        try:
                            quote_fees = float(fee_accounting.get("fees_paid_usdt", 0) or 0)
                            if not math.isfinite(quote_fees) or quote_fees < 0:
                                raise ValueError("invalid_usdt_fee")
                            record.fees_paid = quote_fees
                            record.fee_asset = "USDT"
                            fee_meta["fee_accounting_status"] = "complete"
                            fee_meta["fee_breakdown"] = fee_accounting.get("fee_breakdown") or {}
                        except (TypeError, ValueError, OverflowError):
                            fee_meta["fee_accounting_status"] = "pending"
                            fee_meta["fee_accounting_error"] = "invalid_fee_accounting_result"
                    else:
                        fee_meta["fee_accounting_status"] = "pending"
                        fee_meta["fee_accounting_error"] = str(fee_accounting.get("error") or "fee_history_unavailable")[:160]
                    record.metadata_json = fee_meta
                if not all(math.isfinite(value) and value >= 0 for value in (record.filled_quantity, record.avg_fill_price or 0.0)):
                    record.status = "unknown"
                    record.reason = "Invalid exchange fill quantities; manual reconciliation required."
                    state.emergency_stop = True
                    state.bot_run_state = "stopped"
                    state.autonomous_enabled = False
                    state.bot_stop_reason = "invalid_exchange_fill_data"
                    db.add(state)
                    db.add(record)
                    db.commit()
                    return {"ok": False, "sent_to_exchange": True, "error": "invalid_exchange_fill_data",
                            "order_id": record.id, "status": record.status}
                record.reason = "Exchange acknowledged order submission."
                if record.status == "unknown":
                    state.emergency_stop = True
                    state.bot_run_state = "stopped"
                    state.autonomous_enabled = False
                    state.bot_stop_reason = "binance_order_status_unknown"
                    db.add(state)
                    log_event(
                        db, action="master.emergency_stop", severity="critical",
                        message="Binance acknowledged an order with an unknown status; new orders halted.",
                        details={"order_id": record.id, "exchange_status": exchange_status},
                        environment_mode=environment, market_type=market,
                    )
                db.add(record)
                db.commit()
                return {"ok": record.status != "unknown", "sent_to_exchange": True, "order_id": record.id,
                        "status": record.status, "exchange_order_id": record.exchange_order_id,
                        "execution": result}
            # A transport/HTTP error may mean Binance accepted the order but the
            # response was lost. Persist UNKNOWN and latch emergency stop; never retry.
            error = str(result.get("error") or result.get("reason") or "execution_rejected")
            uncertain = any(token in error.lower() for token in ("timeout", "urlopen", "connection", "http_error", "remote end"))
            record.status = "unknown" if uncertain else "rejected"
            record.reason = error[:500]
            db.add(record)
            if uncertain:
                state.emergency_stop = True
                state.bot_run_state = "stopped"
                state.autonomous_enabled = False
                state.bot_stop_reason = "binance_order_outcome_uncertain"
                log_event(db, action="master.emergency_stop", severity="critical",
                          message="Binance order outcome uncertain; new orders halted and automatic resubmission prohibited.",
                          details={"order_id": record.id, "idempotency_key": key},
                          environment_mode=environment, market_type=market)
            db.commit()
            return {"ok": False, "sent_to_exchange": False, "error": error,
                    "order_id": record.id, "status": record.status, "execution": result}
        except Exception as exc:
            db.rollback()
            # Once the durable idempotency row has been committed, an exception
            # may have happened after Binance received the request. Never leave
            # that row looking safely retryable: persist UNKNOWN and halt entries.
            if record is not None and getattr(record, "id", None) is not None:
                try:
                    persisted = db.get(OrderRecord, record.id)
                    current_state = db.get(SystemState, 1)
                    if persisted is not None and persisted.status in {
                        "pending", "new", "submitted", "partially_filled", "unknown"
                    }:
                        persisted.status = "unknown"
                        persisted.reason = (
                            "Execution raised after the durable submission boundary; "
                            "exchange outcome requires manual reconciliation."
                        )
                        db.add(persisted)
                        if current_state is not None:
                            current_state.emergency_stop = True
                            current_state.bot_run_state = "stopped"
                            current_state.autonomous_enabled = False
                            current_state.bot_stop_reason = "binance_order_outcome_uncertain"
                            db.add(current_state)
                            log_event(
                                db, action="master.emergency_stop", severity="critical",
                                message="Execution raised after durable order creation; outcome uncertain and new orders halted.",
                                details={"order_id": persisted.id},
                                environment_mode=environment, market_type=market,
                            )
                        db.commit()
                        return {
                            "ok": False, "sent_to_exchange": False,
                            "error": "binance_execution_outcome_uncertain",
                            "order_id": persisted.id, "status": "unknown",
                        }
                except Exception:
                    db.rollback()
            return {"ok": False, "sent_to_exchange": False,
                    "error": "binance_execution_internal_error", "detail": str(exc)[:240]}


binance_execution_service = BinanceExecutionService()
