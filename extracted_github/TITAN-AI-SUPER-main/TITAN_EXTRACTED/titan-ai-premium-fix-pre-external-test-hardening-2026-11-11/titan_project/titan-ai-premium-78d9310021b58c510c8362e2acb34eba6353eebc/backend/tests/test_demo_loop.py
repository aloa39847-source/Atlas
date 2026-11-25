from app.models import db as dbmod
from app.models.entities import SystemState
from app.services.orchestrator import TradingOrchestrator
from app.scripts import run_demo_loop
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _use_test_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'demo_loop.db'}", connect_args={"check_same_thread": False}, future=True)
    monkeypatch.setattr(dbmod, "engine", engine)
    monkeypatch.setattr(dbmod, "SessionLocal", sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True))
    monkeypatch.setattr(dbmod, "init_db", lambda: dbmod.Base.metadata.create_all(bind=engine))
    return engine


def test_demo_loop_once_runs_only_demo_spot_and_returns_to_stopped(tmp_path, monkeypatch):
    engine = _use_test_db(tmp_path, monkeypatch)
    captured = {}
    def fake_cycle(self, db, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "analysis_action": "wait", "executed": False, "data_source": "binance_public"}
    monkeypatch.setattr(TradingOrchestrator, "run_cycle", fake_cycle)
    monkeypatch.setattr(run_demo_loop, "_STOP", False)
    try:
        assert run_demo_loop.run(interval_seconds=30, symbols=["BTC/USDT"], once=True) == 0
        assert captured["market"] == "spot"
        assert captured["environment"] == "demo"
        assert captured["execute_shadow"] is True
        db = dbmod.SessionLocal()
        try:
            state = db.get(SystemState, 1)
            assert state.bot_run_state == "stopped"
            assert state.autonomous_enabled is False
        finally:
            db.close()
    finally:
        engine.dispose()


def test_demo_loop_rejects_non_demo_state(tmp_path, monkeypatch):
    engine = _use_test_db(tmp_path, monkeypatch)
    try:
        dbmod.init_db()
        db = dbmod.SessionLocal()
        run_demo_loop.core_service.ensure_bootstrap(db)
        state = db.get(SystemState, 1)
        state.account_target = "testnet"
        db.add(state); db.commit(); db.close()
        try:
            run_demo_loop.run(interval_seconds=30, symbols=["BTC/USDT"], once=True)
            assert False, "non-demo state should be rejected"
        except RuntimeError as exc:
            assert str(exc) == "demo_account_only"
    finally:
        engine.dispose()



def test_demo_loop_rechecks_master_stop_before_each_symbol(tmp_path, monkeypatch):
    engine = _use_test_db(tmp_path, monkeypatch)
    calls = []

    def stop_after_first_symbol(self, db, **kwargs):
        calls.append(kwargs["symbol"])
        state = db.get(SystemState, 1)
        state.bot_run_state = "stopped"
        state.bot_stop_reason = "user_stop"
        db.add(state)
        db.commit()
        return {"ok": True, "analysis_action": "wait", "executed": False}

    monkeypatch.setattr(TradingOrchestrator, "run_cycle", stop_after_first_symbol)
    monkeypatch.setattr(run_demo_loop, "_STOP", False)
    try:
        assert run_demo_loop.run(
            interval_seconds=30, symbols=["BTC/USDT", "ETH/USDT"], once=True
        ) == 0
        assert calls == ["BTC/USDT"]
        db = dbmod.SessionLocal()
        try:
            state = db.get(SystemState, 1)
            assert state.bot_run_state == "stopped"
            assert state.bot_stop_reason == "user_stop"
        finally:
            db.close()
    finally:
        engine.dispose()


def test_demo_loop_halts_until_explicit_restart_after_data_integrity_error(tmp_path, monkeypatch):
    engine = _use_test_db(tmp_path, monkeypatch)
    calls = []

    def broken_cycle(self, db, **kwargs):
        calls.append(kwargs["symbol"])
        return {"ok": False, "error": "insufficient_market_data", "executed": False}

    monkeypatch.setattr(TradingOrchestrator, "run_cycle", broken_cycle)
    monkeypatch.setattr(run_demo_loop, "_STOP", False)
    try:
        assert run_demo_loop.run(
            interval_seconds=30, symbols=["BTC/USDT", "ETH/USDT"], once=False
        ) == 1
        assert calls == ["BTC/USDT"]
        db = dbmod.SessionLocal()
        try:
            state = db.get(SystemState, 1)
            assert state.bot_run_state == "stopped"
            assert state.bot_stop_reason == "data_integrity_failure:insufficient_market_data"
            assert state.autonomous_enabled is False
        finally:
            db.close()
    finally:
        engine.dispose()


def test_demo_loop_rejects_duplicate_running_worker(tmp_path, monkeypatch):
    engine = _use_test_db(tmp_path, monkeypatch)
    try:
        dbmod.init_db()
        db = dbmod.SessionLocal()
        run_demo_loop.core_service.ensure_bootstrap(db)
        state = db.get(SystemState, 1)
        state.bot_run_state = "running"
        db.add(state); db.commit(); db.close()
        try:
            run_demo_loop.run(interval_seconds=30, symbols=["BTC/USDT"], once=True)
            assert False, "duplicate worker should be rejected"
        except RuntimeError as exc:
            assert str(exc) == "demo_worker_already_running"
    finally:
        engine.dispose()


def test_demo_loop_auto_scans_and_forwards_only_ranked_spot_buy_candidates(tmp_path, monkeypatch):
    engine = _use_test_db(tmp_path, monkeypatch)
    calls = []
    scan_calls = []
    from app.services.smart_scanner import SmartScannerService

    def fake_smart_scan(self, db, **kwargs):
        scan_calls.append(kwargs)
        return {
            "ok": True, "analyzed": 3, "skipped": 0,
            "items": [
                {"symbol": "BTC/USDT", "action": "buy", "rank": 1},
                {"symbol": "ETH/USDT", "action": "wait", "rank": 2},
                {"symbol": "SOL/USDT", "action": "sell", "rank": 3},
                {"symbol": "BAD/BTC", "action": "buy", "rank": 4},
            ],
        }

    def fake_cycle(self, db, **kwargs):
        calls.append(kwargs["symbol"])
        return {"ok": True, "analysis_action": "wait", "executed": False}

    monkeypatch.setattr(SmartScannerService, "scan", fake_smart_scan)
    monkeypatch.setattr(TradingOrchestrator, "run_cycle", fake_cycle)
    monkeypatch.setattr(run_demo_loop, "_STOP", False)
    try:
        dbmod.init_db()
        setup_db = dbmod.SessionLocal()
        try:
            run_demo_loop.core_service.ensure_bootstrap(setup_db)
            state = setup_db.get(SystemState, 1)
            state.risk_limits = {**(state.risk_limits or {}), "demo_initial_balance": 275.0}
            setup_db.add(state)
            setup_db.commit()
        finally:
            setup_db.close()
        assert run_demo_loop.run(interval_seconds=30, symbols=None, once=True) == 0
        assert len(scan_calls) == 1
        assert scan_calls[0]["max_candidates"] == 10
        assert scan_calls[0]["min_quote_volume_usdt"] == 1_000_000.0
        assert scan_calls[0]["balance"] == 275.0
        assert scan_calls[0]["risk_per_trade"] == 0.005
        assert scan_calls[0]["max_data_age_seconds"] == 300
        assert calls == ["BTC/USDT"]
    finally:
        engine.dispose()


def test_demo_loop_halts_when_smart_scanner_analysis_is_unavailable(tmp_path, monkeypatch):
    engine = _use_test_db(tmp_path, monkeypatch)
    calls = []
    from app.services.smart_scanner import SmartScannerService
    monkeypatch.setattr(
        SmartScannerService, "scan",
        lambda self, db, **kwargs: (_ for _ in ()).throw(RuntimeError("public discovery unavailable")),
    )
    monkeypatch.setattr(
        TradingOrchestrator, "run_cycle",
        lambda self, db, **kwargs: calls.append(kwargs["symbol"]),
    )
    monkeypatch.setattr(run_demo_loop, "_STOP", False)
    try:
        assert run_demo_loop.run(interval_seconds=30, symbols=None, once=False) == 1
        assert calls == []
        db = dbmod.SessionLocal()
        try:
            state = db.get(SystemState, 1)
            assert state.bot_run_state == "stopped"
            assert state.bot_stop_reason == "scanner_analysis_failure"
            assert state.autonomous_enabled is False
        finally:
            db.close()
    finally:
        engine.dispose()
