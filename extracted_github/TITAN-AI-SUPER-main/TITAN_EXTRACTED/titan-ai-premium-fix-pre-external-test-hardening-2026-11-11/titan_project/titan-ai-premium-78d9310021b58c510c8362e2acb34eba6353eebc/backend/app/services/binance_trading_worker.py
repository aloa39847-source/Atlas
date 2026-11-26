"""Supervised Binance Testnet/Live trading loop for TITAN.

The worker is the only autonomous caller of BinanceExecutionService. It
rechecks persisted control state before every symbol/order, uses read-only
scanners, sizes positions from verified account balances and hard notional
caps, and halts on data, connection, reconciliation, or uncertain-order errors.
No credentials are read by the scanner or exposed in logs.
"""
from __future__ import annotations

import math
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

from sqlalchemy import and_, or_, select

from app.core.config import settings
from app.models import db as dbmod
from app.models.entities import OrderRecord, PositionRecord, SystemState
from app.services.binance_execution import binance_execution_service
from app.services.core_service import core_service
from app.services.audit import log_event
from app.learning.outcomes import record_closed_demo_trade


_ACTIVE_ORDER_STATES = ("pending", "new", "submitted", "partially_filled", "unknown")
_MIN_CONFIDENCE = 0.65
_RISK_PER_TRADE = 0.005


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _flag_stop(db, reason: str, *, emergency: bool = True) -> None:
    state = db.get(SystemState, 1)
    if state is None:
        return
    state.bot_run_state = "stopped"
    state.autonomous_enabled = False
    state.bot_stopped_at = _now()
    state.bot_stop_reason = reason
    if emergency:
        state.emergency_stop = True
    db.add(state)
    log_event(db, action="master.emergency_stop" if emergency else "master.worker_stop",
              severity="critical" if emergency else "warning",
              message=f"Binance worker halted: {reason}.",
              environment_mode=state.account_target, market_type=state.market_type)
    db.commit()


def _read_control() -> dict[str, Any] | None:
    db = dbmod.SessionLocal()
    try:
        state = db.get(SystemState, 1)
        if state is None:
            return None
        return {
            "account_target": str(state.account_target or "demo"),
            "environment_mode": str(state.environment_mode or "demo"),
            "market_type": str(state.market_type or "spot"),
            "bot_run_state": str(state.bot_run_state or "stopped"),
            "emergency_stop": bool(state.emergency_stop),
            "live_confirmed_by_user": bool(state.live_confirmed_by_user),
            "risk_limits": dict(state.risk_limits or {}),
            "trading_paused": bool((state.risk_limits or {}).get("trading_paused", False)),
        }
    finally:
        db.close()


def _floor_step(value: float, step: float) -> float:
    if not math.isfinite(value) or value <= 0:
        return 0.0
    if not math.isfinite(step) or step <= 0:
        return value
    v, s = Decimal(str(value)), Decimal(str(step))
    return float((v / s).to_integral_value(rounding=ROUND_DOWN) * s)


def _account_equity(adapter, market: str) -> tuple[float, float]:
    account = adapter.get_account()
    if not isinstance(account, dict) or account.get("error"):
        raise RuntimeError("binance_account_read_failed")
    if market == "futures":
        available = float(account.get("availableBalance", 0) or 0)
        equity = float(account.get("totalWalletBalance", account.get("totalMarginBalance", available)) or 0)
    else:
        balances = account.get("balances")
        if not isinstance(balances, list):
            raise RuntimeError("spot_account_balances_missing")
        usdt = next((row for row in balances if row.get("asset") == "USDT"), None)
        if not isinstance(usdt, dict):
            raise RuntimeError("spot_usdt_balance_missing")
        available = float(usdt.get("free", 0) or 0)
        equity = available
    if not all(math.isfinite(x) and x > 0 for x in (available, equity)):
        raise RuntimeError("binance_account_equity_invalid")
    return available, equity


def _verify_futures_position_sync(db, adapter, target: str) -> None:
    """Fail closed if Binance has futures exposure TITAN cannot safely manage."""
    account = adapter.get_account()
    if not isinstance(account, dict) or account.get("error"):
        raise RuntimeError("futures_position_sync_account_read_failed")
    remote_rows = account.get("positions")
    if not isinstance(remote_rows, list):
        raise RuntimeError("futures_position_sync_data_missing")
    remote: dict[tuple[str, str], float] = {}
    for row in remote_rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper()
        try:
            amount = float(row.get("positionAmt", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            raise RuntimeError("futures_position_sync_invalid_exchange_quantity")
        if not math.isfinite(amount):
            raise RuntimeError("futures_position_sync_invalid_exchange_quantity")
        if abs(amount) <= 1e-12:
            continue
        position_side = str(row.get("positionSide") or "BOTH").upper()
        side = "short" if amount < 0 or position_side == "SHORT" else "long"
        key = (symbol, side)
        remote[key] = remote.get(key, 0.0) + abs(amount)

    local_rows = db.execute(select(PositionRecord).where(
        PositionRecord.environment_mode == target,
        PositionRecord.market_type == "futures",
        PositionRecord.shadow.is_(False),
        PositionRecord.status == "open",
    )).scalars().all()
    local: dict[tuple[str, str], float] = {}
    for row in local_rows:
        side = "short" if row.side == "short" else "long"
        key = (row.symbol.replace("/", "").upper(), side)
        quantity = float(row.quantity or 0)
        if not math.isfinite(quantity) or quantity < 0:
            raise RuntimeError("futures_position_sync_invalid_local_quantity")
        local[key] = local.get(key, 0.0) + quantity

    keys = set(remote) | set(local)
    mismatches = [
        {"symbol": symbol, "side": side, "exchange_quantity": remote.get((symbol, side), 0.0),
         "titan_quantity": local.get((symbol, side), 0.0)}
        for symbol, side in sorted(keys)
        if abs(remote.get((symbol, side), 0.0) - local.get((symbol, side), 0.0))
           > max(1e-8, remote.get((symbol, side), 0.0) * 1e-5)
    ]
    if mismatches:
        _flag_stop(db, "futures_exchange_position_mismatch", emergency=True)
        raise RuntimeError("futures_exchange_position_mismatch")


def _daily_loss_cap_usdt(equity: float, risk_limits: dict) -> float:
    """Resolve the effective daily loss ceiling; never exceed the approved 7% cap."""
    if not math.isfinite(equity) or equity <= 0:
        return 0.0
    configured_pct = risk_limits.get("max_daily_loss_cap", settings.max_daily_loss_cap)
    try:
        pct = float(configured_pct)
    except (TypeError, ValueError, OverflowError):
        pct = float(settings.max_daily_loss_cap)
    hard_cap_pct = min(float(settings.max_daily_loss_cap), 0.07)
    if not math.isfinite(pct) or pct <= 0:
        pct = hard_cap_pct
    pct = min(pct, hard_cap_pct)
    cap = equity * pct
    configured_usdt = risk_limits.get("max_daily_loss_usdt")
    if configured_usdt is not None:
        try:
            cash_cap = float(configured_usdt)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        if not math.isfinite(cash_cap) or cash_cap <= 0:
            return 0.0
        cap = min(cap, cash_cap)
    return cap


def _daily_loss_exceeded(db, target: str, market: str, equity: float, risk_limits: dict) -> bool:
    cap = _daily_loss_cap_usdt(equity, risk_limits)
    if cap <= 0:
        return True
    cutoff = _now().replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    rows = db.execute(select(PositionRecord).where(
        PositionRecord.environment_mode == target,
        PositionRecord.market_type == market,
        PositionRecord.shadow.is_(False),
        PositionRecord.status == "closed",
        PositionRecord.updated_at >= cutoff,
    )).scalars().all()
    realized = sum(min(0.0, float(row.realized_pnl or 0.0)) for row in rows)
    fee_orders = db.execute(select(OrderRecord).where(
        OrderRecord.environment_mode == target,
        OrderRecord.market_type == market,
        OrderRecord.shadow.is_(False),
        OrderRecord.status.in_(("filled", "partially_filled")),
        OrderRecord.updated_at >= cutoff,
    )).scalars().all()
    fees_total = 0.0
    for order in fee_orders:
        fee_meta = dict(order.metadata_json or {})
        if fee_meta.get("fee_accounting_status") == "pending":
            return True
        try:
            fee = float(order.fees_paid or 0.0)
        except (TypeError, ValueError, OverflowError):
            return True
        if not math.isfinite(fee) or fee < 0:
            return True
        if fee > 0:
            fee_asset = str(order.fee_asset or "").upper()
            if fee_asset != "USDT":
                # Daily limits are denominated in USDT; unknown units fail closed.
                return True
            fees_total += fee
    open_positions = db.execute(select(PositionRecord).where(
        PositionRecord.environment_mode == target,
        PositionRecord.market_type == market,
        PositionRecord.shadow.is_(False),
        PositionRecord.status == "open",
    )).scalars().all()
    realized += sum(min(0.0, float(row.unrealized_pnl or 0.0)) for row in open_positions)
    return abs(realized) + fees_total >= cap


def _position_for(db, target: str, market: str, symbol: str):
    return db.execute(select(PositionRecord).where(
        PositionRecord.environment_mode == target,
        PositionRecord.market_type == market,
        PositionRecord.shadow.is_(False),
        PositionRecord.symbol == symbol,
        PositionRecord.status == "open",
    ).order_by(PositionRecord.id.desc())).scalars().first()


def _has_pending(db, target: str, market: str, symbol: str) -> bool:
    return db.execute(select(OrderRecord.id).where(
        OrderRecord.environment_mode == target,
        OrderRecord.market_type == market,
        OrderRecord.shadow.is_(False),
        OrderRecord.symbol == symbol,
        OrderRecord.status.in_(_ACTIVE_ORDER_STATES),
    ).limit(1)).first() is not None


def _close_position(db, adapter, position: PositionRecord, price: float, target: str, market: str, exit_reason: str = "signal_or_manual") -> dict[str, Any]:
    side = "SELL" if position.side in {"long", "long_spot"} else "BUY"
    key = f"titan-close-{position.id}-{uuid.uuid4().hex[:16]}"
    result = binance_execution_service.execute_order(
        db, symbol=position.symbol, side=side, order_type="MARKET",
        quantity=float(position.quantity), leverage=float(position.leverage or 1.0),
        reduce_only=(market == "futures"), idempotency_key=key,
        live_worker_ready=True,
        order_metadata={"intent": "close", "position_id": position.id},
    )
    if not result.get("ok") or not result.get("sent_to_exchange"):
        return result
    order = db.get(OrderRecord, result.get("order_id"))
    filled = float(order.filled_quantity or 0) if order else 0.0
    if filled <= 0 or (order and order.status not in {"filled", "partially_filled"}):
        return {**result, "position_updated": False, "note": "Close order acknowledged but fill is not confirmed; worker will not assume the position is closed."}
    qty_closed = min(filled, float(position.quantity))
    entry = float(position.entry_avg_price or price)
    actual_exit = float(order.avg_fill_price or price) if order else price
    if not math.isfinite(actual_exit) or actual_exit <= 0:
        raise RuntimeError("close_fill_price_invalid")
    pnl = (actual_exit - entry) * qty_closed if position.side in {"long", "long_spot"} else (entry - actual_exit) * qty_closed
    position.quantity = max(0.0, float(position.quantity) - qty_closed)
    position.realized_pnl = float(position.realized_pnl or 0.0) + pnl
    # The immediate close path has already applied this cumulative fill.
    # Persist the watermark so a later fee-only reconciliation cannot apply it twice.
    close_meta = dict(order.metadata_json or {})
    close_meta["applied_filled_quantity"] = max(
        float(close_meta.get("applied_filled_quantity", 0) or 0), filled,
    )
    order.metadata_json = close_meta
    db.add(order)
    position.mark_price = actual_exit
    if position.quantity <= 1e-12:
        position.quantity = 0.0
        position.status = "closed"
    db.add(position)
    db.commit()
    learning_record = None
    if target == "demo" and position.status == "closed" and order.status == "filled":
        learning_record = record_closed_demo_trade(
            db, position, order, exit_reason=exit_reason,
        )
        if learning_record.get("ok"):
            db.commit()
    if order and (order.status != "filled" or filled + 1e-12 < float(position.quantity) + qty_closed):
        _flag_stop(db, "partial_close_fill_manual_reconciliation_required", emergency=True)
    return {**result, "position_updated": True, "remaining_quantity": position.quantity}


def _manage_positions(db, adapter, scan_items: dict[str, dict], target: str, market: str) -> None:
    positions = db.execute(select(PositionRecord).where(
        PositionRecord.environment_mode == target,
        PositionRecord.market_type == market,
        PositionRecord.shadow.is_(False),
        PositionRecord.status == "open",
    )).scalars().all()
    for position in positions:
        control = _read_control()
        if not control or control["emergency_stop"] or control["bot_run_state"] == "stopped":
            return
        if _has_pending(db, target, market, position.symbol):
            continue
        item = scan_items.get(position.symbol, {})
        raw_price = item.get("mark_price") if market == "futures" else None
        try:
            price = float(raw_price) if raw_price is not None else float(adapter._market_price(position.symbol))
        except Exception:
            raise RuntimeError("open_position_mark_price_unavailable")
        if not math.isfinite(price) or price <= 0:
            raise RuntimeError("open_position_mark_price_invalid")
        entry_for_pnl = float(position.entry_avg_price or price)
        position.mark_price = price
        position.unrealized_pnl = (
            (price - entry_for_pnl) * float(position.quantity)
            if position.side in {"long", "long_spot"}
            else (entry_for_pnl - price) * float(position.quantity)
        )
        db.add(position)
        db.commit()
        meta = position.metadata_json if isinstance(position.metadata_json, dict) else {}
        stop = float(meta.get("stop_loss") or 0)
        take = float(meta.get("take_profit") or 0)
        hit_stop = stop > 0 and (price <= stop if position.side in {"long", "long_spot"} else price >= stop)
        hit_take = take > 0 and (price >= take if position.side in {"long", "long_spot"} else price <= take)
        opposite = str(item.get("action", "wait")).lower()
        reverse_signal = opposite in ({"sell"} if market == "spot" else ({"short"} if position.side == "long" else {"long"}))
        if hit_stop or hit_take or reverse_signal:
            reason = "stop_loss" if hit_stop else "take_profit" if hit_take else "reverse_signal"
            _close_position(db, adapter, position, price, target, market, exit_reason=reason)


def _entry_notional_cap(target: str, market: str, leverage: float, remaining_exposure: float) -> float:
    """Return a per-entry notional ceiling; live cap is defined in margin terms."""
    if not math.isfinite(leverage) or leverage < 1 or not math.isfinite(remaining_exposure):
        return 0.0
    if market == "spot":
        per_entry_notional = settings.max_spot_trade_notional
    elif market == "futures":
        # The hard ceiling is the $30 initial-margin budget multiplied by
        # the leverage selected by the strategy engine, bounded by 10x/$300.
        per_entry_notional = min(
            settings.max_futures_trade_notional,
            settings.max_futures_trade_margin * leverage,
        )
    else:
        return 0.0
    return max(0.0, min(per_entry_notional, remaining_exposure))


def _effective_futures_leverage_cap(risk_limits: dict | None) -> float:
    """Resolve saved Futures leverage without exceeding the system hard cap."""
    raw_cap = risk_limits.get("max_leverage_cap", settings.max_leverage_cap) if isinstance(risk_limits, dict) else settings.max_leverage_cap
    if isinstance(raw_cap, bool) or isinstance(settings.max_leverage_cap, bool):
        raise ValueError("invalid_futures_leverage_cap")
    try:
        configured_cap = float(raw_cap)
        system_cap = float(settings.max_leverage_cap)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid_futures_leverage_cap")
    if not math.isfinite(configured_cap) or not math.isfinite(system_cap) or configured_cap < 1 or system_cap < 1:
        raise ValueError("invalid_futures_leverage_cap")
    return min(configured_cap, system_cap)


def _open_signal(
    db, adapter, item: dict, target: str, market: str,
    available: float, equity: float, risk_limits: dict,
    current_exposure: float, open_count: int,
) -> None:
    symbol = str(item.get("symbol") or "")
    action = str(item.get("action") or "wait").lower()
    allowed = {"buy"} if market == "spot" else {"long", "short"}
    if risk_limits.get("trading_paused") is True:
        return
    if action not in allowed or float(item.get("confidence") or 0) < _MIN_CONFIDENCE:
        return
    if _position_for(db, target, market, symbol) or _has_pending(db, target, market, symbol):
        return
    try:
        max_positions = int(risk_limits.get("max_open_positions_cap", settings.max_open_positions_cap))
        exposure_multiple = float(risk_limits.get("max_portfolio_exposure_multiple", settings.max_portfolio_exposure_multiple))
    except (TypeError, ValueError, OverflowError):
        return
    if max_positions < 1 or max_positions > settings.max_open_positions_cap or not math.isfinite(exposure_multiple) or exposure_multiple <= 0:
        return
    if open_count >= max_positions:
        return
    portfolio_cap = min(equity * exposure_multiple, equity * 2.0)
    remaining_exposure = portfolio_cap - current_exposure
    if not math.isfinite(remaining_exposure) or remaining_exposure <= 0:
        return
    signal_entry = float(item.get("mark_price") or item.get("entry_price") or 0)
    entry = float(item.get("mark_price") or 0) if market == "futures" else float(adapter._market_price(symbol))
    if (not math.isfinite(signal_entry) or signal_entry <= 0 or not math.isfinite(entry) or entry <= 0
            or abs(entry - signal_entry) / signal_entry > 0.03):
        return
    stop = float(item.get("stop_loss") or 0)
    take = float(item.get("take_profit") or 0)
    if not all(math.isfinite(v) and v > 0 for v in (entry, stop, take)):
        return
    gap = abs(entry - stop)
    if gap <= 0 or (action == "buy" or action == "long") and stop >= entry or action == "short" and stop <= entry:
        return
    risk_cash = equity * _RISK_PER_TRADE
    quantity_by_risk = risk_cash / gap
    leverage_cap = _effective_futures_leverage_cap(risk_limits) if market == "futures" else 1.0
    # Use the leverage estimated by the strategy engine; if it supplies no
    # estimate, default to 1x rather than silently assuming a higher leverage.
    leverage = 1.0 if market == "spot" else max(
        1, min(math.floor(leverage_cap), int(float(item.get("leverage") or 1)))
    )
    # Hard initial-margin budget applies to every Futures environment.
    futures_margin_cap = settings.max_futures_trade_margin if market == "futures" else None
    notional_cap = _entry_notional_cap(target, market, leverage, remaining_exposure)
    budget = min(notional_cap, available if market == "spot" else available * leverage)
    if futures_margin_cap is not None:
        budget = min(budget, futures_margin_cap * leverage)
    quantity = min(quantity_by_risk, budget / entry)
    filters = adapter._symbol_filters(symbol)
    quantity = _floor_step(quantity, float(filters.get("marketStepSize") or filters.get("stepSize") or 0))
    notional = quantity * entry
    min_notional = float(filters.get("minNotional") or 0)
    if (quantity <= 0 or not math.isfinite(notional)
            or notional > notional_cap + 1e-8
            or (min_notional > 0 and notional + 1e-8 < min_notional)):
        return
    side = "BUY" if action in {"buy", "long"} else "SELL"
    key = f"titan-open-{uuid.uuid4().hex}"
    result = binance_execution_service.execute_order(
        db, symbol=symbol, side=side, order_type="MARKET",
        quantity=quantity, leverage=float(leverage), reduce_only=False,
        idempotency_key=key, live_worker_ready=True,
        order_metadata={
            "intent": "open", "stop_loss": stop, "take_profit": take,
            "strategy": item.get("strategy"), "decision_id": item.get("decision_id"),
            "confidence": item.get("confidence"), "reference_price": entry,
            "signal_action": action,
            "estimated_initial_margin_usdt": (notional / leverage) if market == "futures" else notional,
            "futures_per_trade_margin_cap_usdt": futures_margin_cap,
        },
    )
    if not result.get("ok") or not result.get("sent_to_exchange"):
        return
    order = db.get(OrderRecord, result.get("order_id"))
    if order is None:
        raise RuntimeError("exchange_order_record_missing_after_submission")
    order.price = entry
    db.add(order)
    db.commit()
    if float(order.filled_quantity or 0) <= 0:
        # Pending market order is tracked by its exchange client ID and polled
        # before any later symbol-level decision; never submit a duplicate.
        return
    filled = float(order.filled_quantity)
    avg = float(order.avg_fill_price or entry)
    pos = PositionRecord(
        environment_mode=target, market_type=market, symbol=symbol,
        side=("long_spot" if market == "spot" else action),
        quantity=filled, entry_avg_price=avg, mark_price=avg,
        leverage=float(leverage), status="open", shadow=False,
        metadata_json={
            "stop_loss": stop, "take_profit": take,
            "strategy": item.get("strategy"), "decision_id": item.get("decision_id"),
            "confidence": item.get("confidence"), "entry_order_id": order.id,
            "entry_idempotency_key": key,
        },
    )
    db.add(pos)
    db.commit()
    # Partial execution is tracked as a position, but the worker stops for
    # manual reconciliation rather than treating a partial market fill as a
    # complete entry or placing another order.
    if order.status != "filled" or filled + 1e-12 < quantity:
        _flag_stop(db, "partial_market_order_fill_manual_reconciliation_required", emergency=True)


def _sync_pending_orders(db, adapter, target: str, market: str) -> None:
    """Poll every durable pending order and materialize exchange fills locally."""
    utc_midnight = _now().replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    pending = db.execute(select(OrderRecord).where(
        OrderRecord.environment_mode == target,
        OrderRecord.market_type == market,
        OrderRecord.shadow.is_(False),
        or_(
            OrderRecord.status.in_(_ACTIVE_ORDER_STATES),
            and_(OrderRecord.status == "filled", OrderRecord.updated_at >= utc_midnight),
        ),
    )).scalars().all()
    for order in pending:
        existing_meta = dict(order.metadata_json or {})
        if order.status == "filled" and existing_meta.get("fee_accounting_status") == "complete":
            continue
        response = adapter.get_order(
            order.symbol, client_order_id=order.client_order_id,
        )
        if not isinstance(response, dict) or response.get("error"):
            order.status = "unknown"
            order.reason = "Order status lookup failed; outcome requires manual reconciliation."
            db.add(order)
            _flag_stop(db, "binance_pending_order_status_unavailable", emergency=True)
            raise RuntimeError("binance_pending_order_status_unavailable")
        raw_status = str(response.get("status") or "").lower()
        status_map = {
            "new": "new", "partially_filled": "partially_filled",
            "filled": "filled", "canceled": "cancelled",
            "cancelled": "cancelled", "expired": "expired",
            "rejected": "rejected",
        }
        status = status_map.get(raw_status)
        try:
            executed = float(response.get("executedQty", 0) or 0)
            average = float(response.get("avgPrice", 0) or 0)
            if average <= 0 and executed > 0:
                quote_total = float(response.get("cummulativeQuoteQty", 0) or 0)
                if quote_total > 0:
                    average = quote_total / executed
        except (TypeError, ValueError, OverflowError):
            status = None
            executed = average = float("nan")
        if (status is None or not math.isfinite(executed) or executed < 0
                or not math.isfinite(average) or average < 0
                or executed > float(order.quantity) + 1e-8):
            order.status = "unknown"
            order.reason = "Invalid or unknown Binance order status/fill data."
            db.add(order)
            _flag_stop(db, "binance_pending_order_status_invalid", emergency=True)
            raise RuntimeError("binance_pending_order_status_invalid")
        order.status = status
        order.filled_quantity = executed
        order.avg_fill_price = average or order.avg_fill_price
        order.exchange_order_id = str(response.get("orderId")) if response.get("orderId") is not None else order.exchange_order_id
        meta = dict(order.metadata_json or {})
        if executed > 0 and hasattr(adapter, "get_order_fee_quote") and (status != "filled" or meta.get("fee_accounting_status") != "complete"):
            fee_accounting = adapter.get_order_fee_quote(
                order.symbol, order.exchange_order_id, fills=response.get("fills"),
            )
            if isinstance(fee_accounting, dict) and fee_accounting.get("ok") is True:
                try:
                    quote_fees = float(fee_accounting.get("fees_paid_usdt", 0) or 0)
                    if not math.isfinite(quote_fees) or quote_fees < 0:
                        raise ValueError("invalid_usdt_fee")
                    order.fees_paid = quote_fees
                    order.fee_asset = "USDT"
                    meta["fee_accounting_status"] = "complete"
                    meta["fee_breakdown"] = fee_accounting.get("fee_breakdown") or {}
                    meta.pop("fee_accounting_error", None)
                except (TypeError, ValueError, OverflowError):
                    meta["fee_accounting_status"] = "pending"
                    meta["fee_accounting_error"] = "invalid_fee_accounting_result"
            else:
                meta["fee_accounting_status"] = "pending"
                meta["fee_accounting_error"] = str((fee_accounting or {}).get("error") or "fee_history_unavailable")[:160]
            order.metadata_json = meta
        db.add(order)
        intent = meta.get("intent")
        if executed > 0 and intent == "open":
            positions = db.execute(select(PositionRecord).where(
                PositionRecord.environment_mode == target,
                PositionRecord.market_type == market,
                PositionRecord.shadow.is_(False),
                PositionRecord.status == "open",
            )).scalars().all()
            position = next((
                row for row in positions
                if isinstance(row.metadata_json, dict)
                and row.metadata_json.get("entry_order_id") == order.id
            ), None)
            side = "long_spot" if market == "spot" else ("long" if order.side == "buy" else "short")
            avg = float(order.avg_fill_price or meta.get("reference_price") or order.price or 0)
            if avg <= 0 or not math.isfinite(avg):
                raise RuntimeError("pending_entry_average_price_invalid")
            if position is None:
                position = PositionRecord(
                    environment_mode=target, market_type=market, symbol=order.symbol,
                    side=side, quantity=executed, entry_avg_price=avg, mark_price=avg,
                    leverage=float((order.metadata_json or {}).get("leverage", 1.0)),
                    status="open", shadow=False,
                    metadata_json={
                        "stop_loss": meta.get("stop_loss"),
                        "take_profit": meta.get("take_profit"),
                        "strategy": meta.get("strategy"),
                        "decision_id": meta.get("decision_id"),
                        "confidence": meta.get("confidence"),
                        "entry_order_id": order.id,
                        "entry_idempotency_key": order.idempotency_key,
                    },
                )
                db.add(position)
            else:
                position.quantity = executed
                position.entry_avg_price = avg
                position.mark_price = avg
                db.add(position)
        elif executed > 0 and intent == "close":
            position_id = meta.get("position_id")
            position = db.get(PositionRecord, int(position_id)) if position_id is not None else None
            if position is None:
                raise RuntimeError("pending_close_position_missing")
            applied = float(meta.get("applied_filled_quantity", 0) or 0)
            delta = max(0.0, executed - applied)
            if delta > 0:
                exit_price = float(order.avg_fill_price or 0)
                entry_price = float(position.entry_avg_price or 0)
                if exit_price <= 0 or entry_price <= 0:
                    raise RuntimeError("pending_close_fill_price_invalid")
                pnl = ((exit_price - entry_price) if position.side in {"long", "long_spot"}
                       else (entry_price - exit_price)) * delta
                position.quantity = max(0.0, float(position.quantity) - delta)
                position.realized_pnl = float(position.realized_pnl or 0.0) + pnl
                meta["applied_filled_quantity"] = executed
                order.metadata_json = meta
                db.add(position)
                db.add(order)
        db.commit()
        if status == "partially_filled" or (status in {"cancelled", "expired"} and executed > 0):
            _flag_stop(db, "partial_exchange_fill_manual_reconciliation_required", emergency=True)
            raise RuntimeError("partial_exchange_fill_manual_reconciliation_required")
        if status == "filled" and intent == "close":
            position_id = meta.get("position_id")
            position = db.get(PositionRecord, int(position_id)) if position_id is not None else None
            if position is not None and position.quantity <= 1e-12:
                position.quantity = 0.0
                position.status = "closed"
                db.add(position)
                db.commit()


def _exposure_snapshot(db, adapter, target: str, market: str, items: dict[str, dict]) -> tuple[float, int]:
    positions = db.execute(select(PositionRecord).where(
        PositionRecord.environment_mode == target,
        PositionRecord.market_type == market,
        PositionRecord.shadow.is_(False),
        PositionRecord.status == "open",
    )).scalars().all()
    exposure = 0.0
    for position in positions:
        item = items.get(position.symbol, {})
        raw_price = item.get("mark_price") if market == "futures" else None
        price = float(raw_price) if raw_price is not None else float(adapter._market_price(position.symbol))
        quantity = float(position.quantity or 0)
        if not math.isfinite(price) or price <= 0 or not math.isfinite(quantity) or quantity < 0:
            raise RuntimeError("open_position_exposure_invalid")
        exposure += price * quantity
    pending = db.execute(select(OrderRecord).where(
        OrderRecord.environment_mode == target,
        OrderRecord.market_type == market,
        OrderRecord.shadow.is_(False),
        OrderRecord.status.in_(_ACTIVE_ORDER_STATES),
    )).scalars().all()
    for order in pending:
        item = items.get(order.symbol, {})
        raw_price = order.price or item.get("mark_price") or item.get("entry_price")
        if raw_price is None:
            raise RuntimeError("pending_order_exposure_price_unavailable")
        price = float(raw_price)
        remaining = max(0.0, float(order.quantity or 0) - float(order.filled_quantity or 0))
        if not math.isfinite(price) or price <= 0:
            raise RuntimeError("pending_order_exposure_invalid")
        exposure += price * remaining
    return exposure, len(positions)


def run(interval_seconds: int = 60, stop_event: threading.Event | None = None) -> int:
    if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int) or interval_seconds < 15:
        raise ValueError("execution_worker_interval_must_be_at_least_15_seconds")
    control = _read_control()
    if not control:
        raise RuntimeError("system_state_missing")
    target, market = control["account_target"], control["market_type"]
    if target not in {"testnet", "live"}:
        raise RuntimeError("binance_worker_requires_testnet_or_live_account_target")
    if target == "live" and not control["live_confirmed_by_user"]:
        raise RuntimeError("live_requires_explicit_user_confirmation")

    # Verify the selected account connection and balances before reporting
    # the worker as running to the API/UI.
    db = dbmod.SessionLocal()
    try:
        state = db.get(SystemState, 1)
        if not state or state.emergency_stop:
            raise RuntimeError("emergency_stop_active_or_state_missing")
        initial_adapter = binance_execution_service.adapter_for(
            state, live_worker_ready=(target == "live"),
        )
        initial_preflight = binance_execution_service.preflight(
            db, live_worker_ready=(target == "live"),
        )
        if not initial_preflight.get("ready"):
            raise RuntimeError("initial_binance_preflight_not_ready")
        _account_equity(initial_adapter, market)
        _sync_pending_orders(db, initial_adapter, target, market)
        if market == "futures":
            _verify_futures_position_sync(db, initial_adapter, target)
        state.bot_run_state = "running"
        state.bot_started_at = _now()
        state.bot_stopped_at = None
        state.bot_stop_reason = f"supervised_binance_{target}_{market}_worker"
        state.autonomous_enabled = True
        db.add(state)
        db.commit()
    finally:
        db.close()

    try:
        while not (stop_event and stop_event.is_set()):
            control = _read_control()
            if (not control or control["emergency_stop"] or control["bot_run_state"] == "stopped"
                    or control["account_target"] != target or control["market_type"] != market):
                return 0
            if target == "live" and not control["live_confirmed_by_user"]:
                raise RuntimeError("live_confirmation_revoked")
            db = dbmod.SessionLocal()
            try:
                state = db.get(SystemState, 1)
                if state is None:
                    raise RuntimeError("system_state_missing")
                adapter = binance_execution_service.adapter_for(state, live_worker_ready=True)
                preflight = binance_execution_service.preflight(db, live_worker_ready=True)
                if not preflight.get("ready"):
                    raise RuntimeError("binance_preflight_not_ready")
                available, equity = _account_equity(adapter, market)
                _sync_pending_orders(db, adapter, target, market)
                if market == "futures":
                    _verify_futures_position_sync(db, adapter, target)
                control = _read_control()
                if not control or control["emergency_stop"] or control["bot_run_state"] == "stopped":
                    return 0
                if _daily_loss_exceeded(db, target, market, equity, control["risk_limits"]):
                    _flag_stop(db, "daily_loss_limit_reached", emergency=True)
                    return 1
                if market == "spot":
                    from app.services.smart_scanner import SmartScannerService
                    scanner = SmartScannerService()
                    scan = scanner.scan(
                        db, max_candidates=10, balance=available,
                        risk_per_trade=_RISK_PER_TRADE, max_data_age_seconds=300,
                        environment_mode=("live" if target == "live" else "demo"),
                    )
                else:
                    from app.services.smart_futures_scanner import SmartFuturesScannerService
                    scanner = SmartFuturesScannerService()
                    scan = scanner.scan(
                        db, max_candidates=10, balance=available,
                        risk_per_trade=_RISK_PER_TRADE, max_data_age_seconds=300,
                        max_leverage=_effective_futures_leverage_cap(control["risk_limits"]),
                        environment_mode=("live" if target == "live" else "demo"),
                    )
                if not scan.get("ok"):
                    raise RuntimeError("market_analysis_failed")
                items = {str(row.get("symbol")): row for row in scan.get("items", []) if isinstance(row, dict)}
                _manage_positions(db, adapter, items, target, market)
                current_exposure, open_count = _exposure_snapshot(db, adapter, target, market, items)
                for item in scan.get("items", []):
                    control = _read_control()
                    if (not control or control["emergency_stop"] or control["bot_run_state"] == "stopped"
                            or control["account_target"] != target or control["market_type"] != market):
                        return 0
                    _open_signal(
                        db, adapter, item, target, market, available, equity,
                        control["risk_limits"], current_exposure, open_count,
                    )
                    # Refresh counters after each accepted order before another
                    # candidate is considered in the same scan cycle.
                    current_exposure, open_count = _exposure_snapshot(db, adapter, target, market, items)
                    latest = _read_control()
                    if not latest or latest["emergency_stop"] or latest["bot_run_state"] == "stopped":
                        return 0
            except Exception as exc:
                db.rollback()
                reason = str(exc).split(":")[0][:120] or "execution_worker_failure"
                _flag_stop(db, reason, emergency=True)
                return 1
            finally:
                db.close()
            if stop_event is not None:
                stop_event.wait(interval_seconds)
            else:
                time.sleep(interval_seconds)
        return 0
    finally:
        db = dbmod.SessionLocal()
        try:
            state = db.get(SystemState, 1)
            if state and state.bot_run_state != "stopped":
                state.bot_run_state = "stopped"
                state.autonomous_enabled = False
                state.bot_stopped_at = _now()
                state.bot_stop_reason = "execution_worker_stopped"
                db.add(state)
                db.commit()
        finally:
            db.close()
