"""Continuous local Demo/Shadow worker for Termux or a server process.

DEMO + SPOT or USD-M FUTURES only. Reads public Binance candles and writes
internal Shadow records. It never reads API keys or sends exchange orders.
Stop it with Ctrl+C or the TITAN Master Stop / Emergency Stop controls.
"""
from __future__ import annotations

import argparse
import json
import math
import signal
import threading
import time
from datetime import datetime, timezone

from app.core.enums import EnvironmentMode, MarketType
from app.models import db as dbmod
from app.models.entities import SystemState
from app.services.orchestrator import TradingOrchestrator
from app.services.core_service import core_service

_STOP = False
_DATA_INTEGRITY_ERRORS = {
    "data_feed_error",
    "analysis_engine_error",
    "stale_or_future_market_data",
    "insufficient_market_data",
    "invalid_market_mark_price",
    "open_position_marks_unavailable",
    "open_position_marks_stale_or_missing",
    "futures_data_feed_error",
    "invalid_or_stale_futures_mark_price",
    "stale_or_future_futures_market_data",
    "futures_analysis_engine_error",
    "futures_open_position_marks_unavailable",
    "futures_open_position_marks_missing",
    "futures_liquidation_state_invalid",
    "futures_isolated_margin_buffer_exhausted",
    "futures_liquidation_price_invalid",
    "futures_estimated_liquidation_close_failed",
    "futures_funding_cursor_invalid",
    "futures_funding_history_unavailable",
    "futures_funding_mark_missing",
    "futures_funding_event_invalid",
    "futures_funding_payment_invalid",
    "futures_funding_accounting_invalid",
    "futures_existing_position_state_invalid",
}


def _request_stop(_signum, _frame):
    global _STOP
    _STOP = True


def _read_state(_db):
    # Use a short-lived independent session rather than the worker's long-lived
    # cycle session. This avoids stale identity-map entries and transaction
    # snapshots hiding a Master Stop / Emergency Stop committed by the API.
    control_db = dbmod.SessionLocal()
    try:
        state = control_db.get(SystemState, 1)
        if state is None:
            return None
        return {
            "emergency_stop": bool(state.emergency_stop),
            "environment_mode": state.environment_mode,
            "market_type": state.market_type,
            "account_target": getattr(state, "account_target", state.environment_mode),
            "bot_run_state": state.bot_run_state,
        }
    finally:
        control_db.close()
def _halt_state(db, reason: str, *, emergency: bool = False) -> None:
    """Persist a stopped state so a later start is explicit and deliberate."""
    state_row = db.get(SystemState, 1)
    if state_row:
        state_row.bot_run_state = "stopped"
        state_row.autonomous_enabled = False
        if emergency:
            state_row.emergency_stop = True
        state_row.bot_stop_reason = reason
        state_row.bot_stopped_at = datetime.now(timezone.utc)
        db.add(state_row)
        db.commit()


def run(interval_seconds: int = 300, symbols: list[str] | None = None, once: bool = False, stop_event: threading.Event | None = None, market: str = "spot") -> int:
    global _STOP
    _STOP = False
    def should_stop() -> bool:
        return _STOP or bool(stop_event and stop_event.is_set())
    if interval_seconds < 30:
        raise ValueError("interval_must_be_at_least_30_seconds")
    if market not in {"spot", "futures"}:
        raise ValueError("unsupported_demo_worker_market")
    auto_scan = symbols is None
    if symbols is not None and (not symbols or any("/" not in s for s in symbols)):
        raise ValueError("symbols_must_use_BASE/QUOTE_format")
    dbmod.init_db()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, _request_stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _request_stop)

    db = dbmod.SessionLocal()
    try:
        core_service.ensure_bootstrap(db)
        db.commit()
        state = _read_state(db)
        if state is None:
            raise RuntimeError("system_state_missing")
        if state["emergency_stop"]:
            raise RuntimeError("emergency_stop_active")
        if state["environment_mode"] != EnvironmentMode.DEMO.value or state["account_target"] != "demo":
            raise RuntimeError("demo_account_only")
        if state["market_type"] != market:
            raise RuntimeError("worker_market_does_not_match_selected_market")
        if state["bot_run_state"] in {"running", "analyzing", "executing"}:
            raise RuntimeError("demo_worker_already_running")
        state_row = db.get(SystemState, 1)
        state_row.bot_run_state = "running"
        state_row.autonomous_enabled = False  # no exchange/autonomous live authority
        state_row.bot_started_at = datetime.now(timezone.utc)
        state_row.bot_stop_reason = f"local_demo_shadow_{market}_worker"
        db.add(state_row); db.commit()
    finally:
        db.close()

    cycle = 0
    exit_code = 0
    halt_reason = None
    orchestrator = TradingOrchestrator()
    try:
        while not should_stop():
            db = dbmod.SessionLocal()
            try:
                state = _read_state(db)
                if state is None or state["emergency_stop"] or state["bot_run_state"] == "stopped":
                    print(json.dumps({"event": "worker_stopped", "state": state}, ensure_ascii=False))
                    break
                if state["environment_mode"] != "demo" or state["account_target"] != "demo" or state["market_type"] != market:
                    halt_reason = "execution_scope_changed_worker_halted"
                    _halt_state(db, halt_reason)
                    break
                cycle_symbols = symbols
                if auto_scan:
                    try:
                        # Market-specific scanner performs bounded public-data
                        # discovery, five-timeframe analysis and ranking. Only
                        # explicit BUY (Spot) or LONG/SHORT (Futures) candidates
                        # reach the orchestrator for a fresh safety check.
                        from app.services.shadow_trading import ShadowTradingService
                        if market == "futures":
                            from app.services.smart_futures_scanner import SmartFuturesScannerService
                            scanner = SmartFuturesScannerService()
                            min_volume = 10_000_000.0
                        else:
                            from app.services.smart_scanner import SmartScannerService
                            scanner = SmartScannerService()
                            min_volume = 1_000_000.0
                        portfolio = ShadowTradingService().get_portfolio(
                            db, env="demo", market=market,
                        )
                        snapshot = portfolio.get("snapshot") if isinstance(portfolio, dict) else None
                        balance = snapshot.get("equity_estimate") if isinstance(snapshot, dict) else None
                        try:
                            balance = float(balance)
                        except (TypeError, ValueError, OverflowError):
                            balance = float("nan")
                        if not math.isfinite(balance) or balance <= 0:
                            raise RuntimeError("demo_market_equity_unavailable_for_scanner")
                        scan_kwargs = {
                            "max_candidates": 10,
                            "min_quote_volume_usdt": min_volume,
                            "balance": balance,
                            "risk_per_trade": 0.005,
                            "max_data_age_seconds": 300,
                        }
                        if market == "futures":
                            scan_kwargs["max_leverage"] = 10.0
                        scan_result = scanner.scan(db, **scan_kwargs)
                        if not scan_result.get("ok"):
                            raise RuntimeError("smart_scanner_analysis_failed")
                        actionable_symbols = [
                            str(item.get("symbol")) for item in scan_result.get("items", [])
                            if isinstance(item, dict)
                            and str(item.get("symbol", "")).endswith("/USDT")
                            and str(item.get("action", "")).lower() in ({"long", "short"} if market == "futures" else {"buy"})
                        ]
                        # Existing positions are always cycled first, even when
                        # the scanner has no fresh entry signal, so stops,
                        # liquidation estimates and mark-price checks keep running.
                        open_position_symbols = [
                            str(item.get("symbol")) for item in portfolio.get("positions", [])
                            if isinstance(item, dict) and str(item.get("symbol", "")).endswith("/USDT")
                        ]
                        cycle_symbols = list(dict.fromkeys(open_position_symbols + actionable_symbols))
                        print(json.dumps({
                            "event": "smart_scan_complete",
                            "market": market,
                            "analyzed": scan_result.get("analyzed", 0),
                            "skipped": scan_result.get("skipped", 0),
                            "actionable_candidates": len(cycle_symbols),
                            "ranked_symbols": cycle_symbols,
                            "source": "binance_futures_public_read_only" if market == "futures" else "binance_public_read_only",
                        }, ensure_ascii=False), flush=True)
                    except Exception as exc:
                        halt_reason = "scanner_analysis_failure"
                        _halt_state(db, halt_reason, emergency=True)
                        exit_code = 1
                        print(json.dumps({"event": "worker_halted", "reason": halt_reason,
                                          "detail": str(exc)[:240]}, ensure_ascii=False), flush=True)
                        break
                    if not cycle_symbols:
                        print(json.dumps({"event": "scanner_no_actionable_candidates",
                                          "market": market,
                                          "source": "binance_futures_public_read_only" if market == "futures" else "binance_public_read_only"},
                                          ensure_ascii=False), flush=True)
                for symbol in cycle_symbols or []:
                    if should_stop():
                        halt_reason = "worker_interrupted"
                        break
                    # Re-read shared control state before EVERY symbol. A Master
                    # Stop or emergency stop during a multi-symbol batch must
                    # prevent all subsequent cycles in that batch.
                    state = _read_state(db)
                    if state is None or state["emergency_stop"] or state["bot_run_state"] == "stopped":
                        halt_reason = "emergency_stop" if state and state["emergency_stop"] else "master_stop"
                        break
                    if state["environment_mode"] != "demo" or state["account_target"] != "demo" or state["market_type"] != market:
                        halt_reason = "execution_scope_changed_worker_halted"
                        _halt_state(db, halt_reason)
                        break

                    result = orchestrator.run_cycle(
                        db, symbol=symbol, market=market, environment="demo", execute_shadow=True,
                        max_data_age_seconds=300,
                        require_active_worker=True,
                    )
                    db.commit()
                    cycle += 1
                    print(json.dumps({"cycle": cycle, **result}, ensure_ascii=False, default=str), flush=True)
                    if not result.get("ok") and result.get("error") in _DATA_INTEGRITY_ERRORS:
                        # Fail closed: stop this worker after any data/analysis
                        # integrity failure. Recovery requires an explicit restart.
                        db.rollback()
                        halt_reason = f"data_integrity_failure:{result.get('error')}"
                        _halt_state(db, halt_reason, emergency=True)
                        exit_code = 2 if once else 1
                        break
                if halt_reason:
                    break
                if once:
                    return 0
            finally:
                db.close()
            for _ in range(interval_seconds):
                if should_stop():
                    break
                if stop_event is not None:
                    stop_event.wait(1.0)
                else:
                    time.sleep(1)
        return exit_code
    finally:
        db = dbmod.SessionLocal()
        try:
            state_row = db.get(SystemState, 1)
            # Preserve a stop reason written by Master Stop, Emergency Stop,
            # scope validation, or the fail-closed data guard.
            if state_row and state_row.bot_run_state != "stopped":
                state_row.bot_run_state = "stopped"
                state_row.autonomous_enabled = False
                state_row.bot_stop_reason = "worker_interrupted_or_stopped"
                state_row.bot_stopped_at = datetime.now(timezone.utc)
                db.add(state_row); db.commit()
        finally:
            db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="TITAN safe Demo/Shadow Spot or Futures worker")
    parser.add_argument("--interval", type=int, default=300, help="seconds between cycles (minimum 30)")
    parser.add_argument("--symbols", default="auto", help="auto for liquid USDT Spot discovery, or comma-separated BASE/QUOTE symbols")
    parser.add_argument("--once", action="store_true", help="run one cycle then exit")
    parser.add_argument("--market", choices=("spot", "futures"), default="spot", help="must match the selected Demo market in system state")
    args = parser.parse_args()
    symbols = None if args.symbols.strip().lower() == "auto" else [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    return run(args.interval, symbols, args.once, market=args.market)


if __name__ == "__main__":
    raise SystemExit(main())
