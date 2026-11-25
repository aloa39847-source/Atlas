"""Single supervised lifecycle for TITAN Demo, Binance Testnet, and Live workers.

Only one worker may own the trading loop at a time. The selected account
target is passed explicitly and revalidated by the worker before any action.
"""
from __future__ import annotations

import threading
import time
from typing import Any


class TradingWorkerSupervisor:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._last_exit_code: int | None = None
        self._last_error: str | None = None
        self._symbols: list[str] | None = None
        self._market: str = "spot"
        self._account_target: str = "demo"

    def start(
        self, *, interval_seconds: int = 60, symbols: list[str] | None = None,
        market: str = "spot", account_target: str = "demo",
    ) -> dict[str, Any]:
        if market not in {"spot", "futures"}:
            return {"ok": False, "error": "unsupported_worker_market"}
        if account_target not in {"demo", "testnet", "live"}:
            return {"ok": False, "error": "unsupported_account_target"}
        if account_target != "demo" and symbols is not None:
            return {"ok": False, "error": "fixed_symbols_only_supported_for_internal_demo"}
        if account_target == "demo" and symbols is not None and (
            not symbols or any("/" not in item for item in symbols)
        ):
            return {"ok": False, "error": "invalid_worker_symbols"}
        if interval_seconds < 15:
            return {"ok": False, "error": "worker_interval_too_short"}

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"ok": False, "error": "demo_worker_already_running" if self._account_target == "demo" else "trading_worker_already_running"}
            self._stop_event = threading.Event()
            self._last_exit_code = None
            self._last_error = None
            self._symbols = list(symbols) if symbols is not None else None
            self._market = market
            self._account_target = account_target
            stop_event = self._stop_event

            def target() -> None:
                try:
                    if account_target == "demo":
                        from app.scripts.run_demo_loop import run
                        self._last_exit_code = run(
                            interval_seconds=max(30, interval_seconds),
                            symbols=symbols, once=False, stop_event=stop_event,
                            market=market,
                        )
                    elif account_target == "live":
                        from app.models import db as dbmod
                        from app.models.entities import SystemState
                        session = dbmod.SessionLocal()
                        try:
                            selected = session.get(SystemState, 1)
                            live_confirmed = bool(selected and selected.live_confirmed_by_user)
                        finally:
                            session.close()
                        if live_confirmed:
                            from app.services.binance_trading_worker import run
                        else:
                            from app.services.live_analysis_worker import run
                        self._last_exit_code = run(
                            interval_seconds=interval_seconds,
                            stop_event=stop_event,
                        )
                    else:
                        from app.services.binance_trading_worker import run
                        self._last_exit_code = run(
                            interval_seconds=interval_seconds,
                            stop_event=stop_event,
                        )
                except Exception as exc:
                    self._last_error = f"{type(exc).__name__}:{exc}"
                    try:
                        from app.models import db as dbmod
                        from app.models.entities import SystemState
                        from datetime import datetime, timezone
                        detail = str(exc).lower()
                        startup_configuration_failure = any(token in detail for token in (
                            "initial_binance_preflight_not_ready",
                            "binance_adapter_unavailable",
                            "binance_account_read_failed",
                            "binance_account_equity_invalid",
                            "missing_api_secret",
                            "testnet_credentials_missing",
                            "live_read_only_account_connection_failed",
                            "live_read_only_account_balance_unavailable",
                            "missing_api_key",
                        ))
                        db = dbmod.SessionLocal()
                        try:
                            state = db.get(SystemState, 1)
                            if state is not None:
                                state.bot_run_state = "stopped"
                                state.autonomous_enabled = False
                                # A failed Live worker must never retain a reusable
                                # approval after its execution loop has exited.
                                state.live_confirmed_by_user = False
                                if not startup_configuration_failure:
                                    state.emergency_stop = True
                                state.bot_stop_reason = (
                                    "worker_startup_configuration_failed" if startup_configuration_failure
                                    else "worker_start_or_runtime_failure"
                                )
                                state.bot_stopped_at = datetime.now(timezone.utc)
                                db.add(state)
                                db.commit()
                        finally:
                            db.close()
                    except Exception:
                        pass

            self._thread = threading.Thread(
                target=target,
                name=f"titan-{account_target}-{market}-worker",
                daemon=False,
            )
            self._thread.start()

        # Report success only after the selected worker sets the shared state
        # to running; a fast preflight/startup failure is never reported as OK.
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            thread = self._thread
            if thread is None or not thread.is_alive():
                return {
                    "ok": False, "error": "trading_worker_exited_during_startup",
                    "exit_code": self._last_exit_code, "detail": self._last_error,
                }
            try:
                from app.models import db as dbmod
                from app.models.entities import SystemState
                db = dbmod.SessionLocal()
                try:
                    state = db.get(SystemState, 1)
                    if state is not None and state.bot_run_state in {
                        "running", "analyzing", "executing", "waiting_data",
                    }:
                        return {
                            "ok": True, "running": True, "thread_name": thread.name,
                            "account_target": account_target, "market": market,
                        }
                    if state is not None and state.bot_run_state == "stopped":
                        return {
                            "ok": False, "error": "trading_worker_stopped_during_startup",
                            "stop_reason": state.bot_stop_reason, "detail": self._last_error,
                        }
                finally:
                    db.close()
            except Exception:
                pass
            time.sleep(0.05)
        self.stop(timeout=3.0)
        return {"ok": False, "error": "trading_worker_startup_timeout"}

    def stop(self, *, timeout: float = 10.0) -> dict[str, Any]:
        with self._lock:
            thread = self._thread
            event = self._stop_event
            if thread is None or not thread.is_alive():
                return {"ok": True, "stopped": True, "already_stopped": True}
            if event is not None:
                event.set()
        thread.join(timeout=max(0.0, float(timeout)))
        stopped = not thread.is_alive()
        return {
            "ok": stopped, "stopped": stopped,
            "error": None if stopped else "trading_worker_stop_timeout",
            "thread_name": thread.name,
        }

    def status(self) -> dict[str, Any]:
        thread = self._thread
        alive = bool(thread and thread.is_alive())
        return {
            "running": alive,
            "thread_name": thread.name if alive and thread else None,
            "symbol_mode": ("auto_smart_scan" if self._symbols is None else "fixed_symbols") if alive else None,
            "market": self._market if alive else None,
            "account_target": self._account_target if alive else None,
            "last_exit_code": self._last_exit_code,
            "last_error": self._last_error,
        }


# Keep the old import name so existing routes and shutdown hooks stay compatible.
DemoWorkerSupervisor = TradingWorkerSupervisor
demo_worker_supervisor = TradingWorkerSupervisor()
