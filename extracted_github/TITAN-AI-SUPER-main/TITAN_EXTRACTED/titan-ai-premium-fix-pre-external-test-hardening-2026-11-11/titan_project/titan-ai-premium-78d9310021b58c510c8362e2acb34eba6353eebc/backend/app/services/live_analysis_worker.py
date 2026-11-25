from __future__ import annotations

import math
import threading
from datetime import datetime, timezone

from app.models import db as dbmod
from app.models.entities import SystemState
from app.services.binance_execution import binance_execution_service
from app.services.core_service import core_service


DEFAULT_SYMBOLS = ("BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT")


def _account_balance(adapter, market: str) -> float:
    account = adapter.get_account()
    if not isinstance(account, dict) or account.get("error"):
        raise RuntimeError("live_read_only_account_balance_unavailable")
    if market == "futures":
        raw = account.get("availableBalance") or account.get("totalWalletBalance") or 0
        value = float(raw)
    else:
        balances = account.get("balances") or []
        quote = next((row for row in balances if row.get("asset") == "USDT"), {})
        value = float(quote.get("free", 0) or 0) + float(quote.get("locked", 0) or 0)
    if not math.isfinite(value) or value < 0:
        raise RuntimeError("live_read_only_account_balance_invalid")
    return max(value, 1.0)


def run(interval_seconds: int = 60, stop_event: threading.Event | None = None) -> int:
    if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int) or interval_seconds < 15:
        raise ValueError("live_analysis_worker_interval_must_be_at_least_15_seconds")
    stop_event = stop_event or threading.Event()
    db = dbmod.SessionLocal()
    try:
        state = db.get(SystemState, 1)
        if state is None or str(state.account_target or "").lower() != "live":
            raise RuntimeError("live_analysis_worker_requires_live_account")
        if state.emergency_stop:
            raise RuntimeError("emergency_stop_active")
        if bool(state.live_confirmed_by_user):
            raise RuntimeError("live_execution_already_confirmed_use_execution_worker")
        market = str(state.market_type or "spot").lower()
        adapter = binance_execution_service.adapter_for(state, live_worker_ready=False)
        status = adapter.status()
        if not status.get("connected"):
            raise RuntimeError("live_read_only_account_connection_failed")
        balance = _account_balance(adapter, market)
        state.bot_run_state = "running"
        state.autonomous_enabled = False
        state.bot_stop_reason = "live_read_only_analysis_active"
        state.bot_started_at = datetime.now(timezone.utc)
        state.bot_stopped_at = None
        db.add(state)
        db.commit()
    finally:
        db.close()

    while not stop_event.is_set():
        session = dbmod.SessionLocal()
        try:
            state = session.get(SystemState, 1)
            if state is None or str(state.account_target or "").lower() != "live":
                break
            if state.emergency_stop or bool(state.live_confirmed_by_user):
                break
            market = str(state.market_type or "spot").lower()
            adapter = binance_execution_service.adapter_for(state, live_worker_ready=False)
            balance = _account_balance(adapter, market)
            for symbol in DEFAULT_SYMBOLS:
                if stop_event.is_set():
                    break
                batches = core_service.market_data.get_public_multi_timeframe(
                    symbol, market_type=market,
                )
                analysis = core_service.analyze(
                    session, symbol=symbol, market_type=market,
                    environment_mode="live", balance=balance,
                    max_leverage=1.0, risk_per_trade=0.005,
                    data_source="binance_public", market_batches=batches,
                )
                if not isinstance(analysis, dict) or analysis.get("error"):
                    raise RuntimeError("live_read_only_analysis_failed")
            session.commit()
        except Exception as exc:
            session.rollback()
            state = session.get(SystemState, 1)
            if state is not None:
                state.bot_run_state = "stopped"
                state.autonomous_enabled = False
                state.bot_stop_reason = "live_read_only_analysis_failed:" + str(exc)[:160]
                state.bot_stopped_at = datetime.now(timezone.utc)
                session.add(state)
                session.commit()
            raise
        finally:
            session.close()
        if stop_event.wait(interval_seconds):
            break

    final_db = dbmod.SessionLocal()
    try:
        state = final_db.get(SystemState, 1)
        if state is not None and state.bot_run_state in {"starting", "running", "analyzing", "waiting_data"}:
            state.bot_run_state = "stopped"
            state.autonomous_enabled = False
            state.bot_stop_reason = "live_read_only_observer_stopped"
            state.bot_stopped_at = datetime.now(timezone.utc)
            final_db.add(state)
            final_db.commit()
    finally:
        final_db.close()
    return 0
