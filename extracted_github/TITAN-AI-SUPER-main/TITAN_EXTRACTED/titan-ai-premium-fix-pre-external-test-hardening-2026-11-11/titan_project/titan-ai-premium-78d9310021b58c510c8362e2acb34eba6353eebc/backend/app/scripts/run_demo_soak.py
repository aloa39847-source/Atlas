"""Thirty-minute supervised internal Demo/Shadow worker stability test.

Uses only public Binance market data and an isolated CI database. It never
loads API credentials or sends orders to an exchange.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

from app.models import db as dbmod
from app.models.entities import SystemState
from app.services.core_service import core_service
from app.scripts.run_demo_loop import run as run_demo_loop

SOAK_SECONDS = 30 * 60
WORKER_INTERVAL_SECONDS = 120
SYMBOLS = ["BTC/USDT", "ETH/USDT"]


def main() -> int:
    dbmod.init_db()
    db = dbmod.SessionLocal()
    try:
        core_service.ensure_bootstrap(db)
        state = db.get(SystemState, 1)
        if state is None:
            raise RuntimeError("system_state_missing")
        if state.bot_run_state in {"starting", "running", "analyzing", "executing", "waiting_data"}:
            raise RuntimeError("unexpected_active_worker_in_soak_database")
        state.environment_mode = "demo"
        state.account_target = "demo"
        state.market_type = "spot"
        state.bot_run_state = "starting"
        state.autonomous_enabled = False
        state.emergency_stop = False
        state.live_confirmed_by_user = False
        state.risk_limits = {
            **dict(state.risk_limits or {}),
            "demo_initial_balance": 10_000.0,
            "demo_risk_per_trade": 0.005,
        }
        db.add(state)
        db.commit()
    finally:
        db.close()

    stop_event = threading.Event()
    result: list[int] = []
    errors: list[str] = []

    def worker() -> None:
        try:
            result.append(run_demo_loop(
                interval_seconds=WORKER_INTERVAL_SECONDS,
                symbols=SYMBOLS,
                once=False,
                stop_event=stop_event,
                market="spot",
            ))
        except Exception as exc:
            errors.append(f"{type(exc).__name__}:{exc}")

    thread = threading.Thread(target=worker, name="atlas-demo-soak", daemon=False)
    started_at = datetime.now(timezone.utc).isoformat()
    deadline = time.monotonic() + SOAK_SECONDS
    checks = 0
    worker_seen_running = False
    startup_deadline = time.monotonic() + 90.0
    thread.start()
    try:
        while time.monotonic() < deadline:
            if not thread.is_alive():
                raise RuntimeError("demo_worker_exited_during_soak:" + (errors[-1] if errors else str(result)))
            monitor = dbmod.SessionLocal()
            try:
                state = monitor.get(SystemState, 1)
                if state is None:
                    raise RuntimeError("system_state_disappeared_during_soak")
                if state.emergency_stop:
                    raise RuntimeError("emergency_stop_latched_during_demo_soak")
                if state.account_target != "demo" or state.environment_mode != "demo" or state.market_type != "spot":
                    raise RuntimeError("demo_execution_scope_changed_during_soak")
                if state.bot_run_state in {"running", "analyzing", "executing", "waiting_data"}:
                    worker_seen_running = True
                elif state.bot_run_state == "starting" and not worker_seen_running and time.monotonic() < startup_deadline:
                    time.sleep(2.0)
                    continue
                else:
                    raise RuntimeError("demo_worker_not_running_during_soak:" + str(state.bot_run_state))
                checks += 1
            finally:
                monitor.close()
            time.sleep(min(15.0, max(0.0, deadline - time.monotonic())))
    finally:
        stop_event.set()
        thread.join(timeout=90.0)

    if thread.is_alive():
        raise RuntimeError("demo_worker_failed_to_stop_after_soak")
    if errors:
        raise RuntimeError("demo_worker_error:" + errors[-1])
    if result != [0]:
        raise RuntimeError("demo_worker_nonzero_exit:" + repr(result))

    report = {
        "test": "30_minute_demo_shadow_worker_soak",
        "started_at": started_at,
        "duration_seconds": SOAK_SECONDS,
        "monitor_checks": checks,
        "symbols": SYMBOLS,
        "worker_interval_seconds": WORKER_INTERVAL_SECONDS,
        "exchange_orders_sent": False,
        "private_binance_account_accessed": False,
        "database_scope": "isolated_ci_only",
        "worker_exit_code": result[0],
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
