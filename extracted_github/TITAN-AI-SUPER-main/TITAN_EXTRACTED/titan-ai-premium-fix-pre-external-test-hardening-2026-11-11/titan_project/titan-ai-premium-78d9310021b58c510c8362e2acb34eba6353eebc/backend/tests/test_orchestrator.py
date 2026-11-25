from app.models import db as dbmod
from app.services.core_service import core_service
from app.services.orchestrator import TradingOrchestrator
from app.services.shadow_trading import ShadowTradingService
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _db(tmp_path):
    db_path = tmp_path / "orchestrator.db"
    dbmod.engine.dispose()
    dbmod.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, future=True)
    dbmod.SessionLocal = sessionmaker(bind=dbmod.engine, autoflush=False, autocommit=False, future=True)
    dbmod.Base.metadata.create_all(bind=dbmod.engine)
    return dbmod.SessionLocal()


def _select_futures_market(db):
    from app.models.entities import SystemState
    core_service.ensure_bootstrap(db)
    state = db.get(SystemState, 1)
    state.market_type = "futures"
    db.add(state)
    db.commit()


def test_orchestrator_wait_does_not_create_shadow_order(tmp_path, monkeypatch):
    db = _db(tmp_path)
    try:
        monkeypatch.setattr(core_service.market_data, "get_public_multi_timeframe", lambda symbol: core_service.market_data.get_multi_timeframe(symbol, seed=42))
        monkeypatch.setattr(core_service, "analyze", lambda *a, **k: {"action": "wait", "decision_id": 10})
        result = TradingOrchestrator().run_cycle(db, symbol="BTC/USDT", execute_shadow=True)
        db.commit()
        assert result["ok"] is True
        assert result["executed"] is False
        assert result["execution_result"]["message"] == "wait_no_trade"
        assert ShadowTradingService().get_portfolio(db, env="demo", market="spot")["orders"] == []
    finally:
        db.close()


def test_orchestrator_executes_only_risk_sized_shadow_spot_buy(tmp_path, monkeypatch):
    db = _db(tmp_path)
    try:
        fake = {"action": "buy", "decision_id": 11, "entry_price": 100.0,
                "risk": {"ok": True, "quantity_estimate": 0.1}}
        monkeypatch.setattr(core_service.market_data, "get_public_multi_timeframe", lambda symbol: core_service.market_data.get_multi_timeframe(symbol, seed=42))
        monkeypatch.setattr(core_service, "analyze", lambda *a, **k: fake)
        result = TradingOrchestrator().run_cycle(db, symbol="BTC/USDT", execute_shadow=True)
        db.commit()
        assert result["executed"] is True
        assert result["execution_mode"] == "shadow_safe"
        portfolio = ShadowTradingService().get_portfolio(db, env="demo", market="spot")
        assert len(portfolio["orders"]) == 1
        assert portfolio["orders"][0]["side"] == "buy"
        assert portfolio["orders"][0]["filled_quantity"] == 0.1
    finally:
        db.close()


def test_orchestrator_rejects_non_spot_or_non_demo(tmp_path):
    db = _db(tmp_path)
    try:
        service = TradingOrchestrator()
        assert service.run_cycle(db, symbol="BTC/USDT", market="futures")["error"] == "demo_futures_selected_state_required"
        assert service.run_cycle(db, symbol="BTC/USDT", environment="live")["error"] == "demo_environment_required"
    finally:
        db.close()


def test_orchestrator_same_candle_is_idempotent(tmp_path, monkeypatch):
    db = _db(tmp_path)
    try:
        monkeypatch.setattr(core_service.market_data, "get_public_multi_timeframe", lambda symbol: core_service.market_data.get_multi_timeframe(symbol, seed=42))
        fake = {"action": "buy", "decision_id": 12, "entry_price": 100.0,
                "risk": {"ok": True, "quantity_estimate": 0.1}}
        monkeypatch.setattr(core_service, "analyze", lambda *a, **k: fake)
        service = TradingOrchestrator()
        first = service.run_cycle(db, symbol="BTC/USDT", execute_shadow=True)
        db.commit()
        second = service.run_cycle(db, symbol="BTC/USDT", execute_shadow=True)
        db.commit()
        portfolio = ShadowTradingService().get_portfolio(db, env="demo", market="spot")
        assert first["executed"] is True and second["executed"] is True
        assert second["execution_result"]["details"].get("duplicate") is True
        assert len(portfolio["orders"]) == 1
    finally:
        db.close()


def test_orchestrator_uses_approved_demo_balance_and_spot_risk(tmp_path, monkeypatch):
    db = _db(tmp_path)
    try:
        monkeypatch.setattr(core_service.market_data, "get_public_multi_timeframe", lambda symbol: core_service.market_data.get_multi_timeframe(symbol, seed=42))
        captured = {}
        def fake_analyze(*args, **kwargs):
            captured.update(kwargs)
            return {"action": "wait", "decision_id": 99}
        monkeypatch.setattr(core_service, "analyze", fake_analyze)
        result = TradingOrchestrator().run_cycle(db, symbol="BTC/USDT", execute_shadow=False)
        assert result["ok"] is True
        assert captured["balance"] == 150.0
        assert captured["max_leverage"] == 1.0
        assert captured["risk_per_trade"] == 0.005
    finally:
        db.close()


def test_orchestrator_sell_decision_closes_existing_spot_shadow_position(tmp_path, monkeypatch):
    db = _db(tmp_path)
    try:
        shadow = ShadowTradingService()
        buy = shadow.create_shadow_order(db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.1, price=100, reference_price=100,
            idempotency_key="orchestrator-exit-entry-001")
        db.commit()
        assert buy.ok
        monkeypatch.setattr(core_service.market_data, "get_public_multi_timeframe", lambda symbol: core_service.market_data.get_multi_timeframe(symbol, seed=42))
        monkeypatch.setattr(core_service, "analyze", lambda *a, **k: {"action": "sell", "decision_id": 101, "entry_price": 110})
        result = TradingOrchestrator().run_cycle(db, symbol="BTC/USDT", execute_shadow=True)
        db.commit()
        assert result["executed"] is True
        assert result["execution_result"]["message"] == "spot_positions_closed"
        portfolio = shadow.get_portfolio(db, env="demo", market="spot")
        assert portfolio["snapshot"]["positions_open"] == 0
        assert len(portfolio["orders"]) == 2
        assert portfolio["orders"][0]["side"] == "sell"
    finally:
        db.close()


def test_orchestrator_scales_spot_risk_to_remaining_quote_cash(tmp_path, monkeypatch):
    db = _db(tmp_path)
    try:
        shadow = ShadowTradingService()
        first = shadow.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
            quantity=0.6, price=100.0, reference_price=100.0,
            idempotency_key="cash-budget-existing-position", fee_rate=0.001,
        )
        db.commit()
        assert first.ok
        available_cash = shadow.get_available_cash(db, env="demo", market="spot")
        assert round(available_cash, 8) == 89.94

        monkeypatch.setattr(
            core_service.market_data, "get_public_multi_timeframe",
            lambda symbol: core_service.market_data.get_multi_timeframe(symbol, seed=42),
        )
        monkeypatch.setattr(core_service.market_data, "get_public_last_prices", lambda symbols: {"BTC/USDT": 100.0})
        captured = {}
        def fake_analyze(*args, **kwargs):
            captured.update(kwargs)
            return {"action": "wait", "decision_id": 777}
        monkeypatch.setattr(core_service, "analyze", fake_analyze)

        result = TradingOrchestrator().run_cycle(db, symbol="ETH/USDT", execute_shadow=False)
        assert result["ok"] is True
        assert round(captured["balance"], 8) == 150.0
        assert round(captured["risk_per_trade"], 8) == round(0.005 * (89.94 / 150.0), 8)
        assert result["risk_budget"]["available_quote_cash"] == 89.94
        assert result["risk_budget"]["allocation_ratio"] == round(89.94 / 150.0, 8)
    finally:
        db.close()


def test_orchestrator_blocks_new_entry_when_other_position_mark_is_stale(tmp_path, monkeypatch):
    db = _db(tmp_path)
    try:
        shadow = ShadowTradingService()
        buy = shadow.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
            quantity=0.1, price=100.0, reference_price=100.0,
            idempotency_key="stale-mark-existing-position",
        )
        db.commit()
        assert buy.ok
        monkeypatch.setattr(
            core_service.market_data, "get_public_multi_timeframe",
            lambda symbol: core_service.market_data.get_multi_timeframe(symbol, seed=42),
        )
        monkeypatch.setattr(core_service.market_data, "get_public_last_prices", lambda symbols: {})
        called = {"analyze": False}
        def forbidden_analysis(*args, **kwargs):
            called["analyze"] = True
            raise AssertionError("analysis must not proceed with stale open-position marks")
        monkeypatch.setattr(core_service, "analyze", forbidden_analysis)

        result = TradingOrchestrator().run_cycle(db, symbol="ETH/USDT", execute_shadow=True)
        assert result["ok"] is False
        assert result["error"] == "open_position_marks_stale_or_missing"
        assert result["missing_symbols"] == ["BTC/USDT"]
        assert result["executed"] is False
        assert called["analyze"] is False
    finally:
        db.close()



def test_public_data_failure_latches_independent_emergency_stop(tmp_path, monkeypatch):
    db = _db(tmp_path)
    try:
        monkeypatch.setattr(
            core_service.market_data, "get_public_multi_timeframe",
            lambda symbol: (_ for _ in ()).throw(ConnectionError("simulated Binance public API outage")),
        )
        service = TradingOrchestrator()
        failed = service.run_cycle(db, symbol="BTC/USDT", execute_shadow=True)
        assert failed["error"] == "data_feed_error"
        assert failed["emergency_stop_latched"] is True
        state = db.get(__import__("app.models.entities", fromlist=["SystemState"]).SystemState, 1)
        assert state.emergency_stop is True
        assert state.bot_run_state == "stopped"
        assert state.autonomous_enabled is False

        monkeypatch.setattr(
            core_service.market_data, "get_public_multi_timeframe",
            lambda symbol: core_service.market_data.get_multi_timeframe(symbol, seed=42),
        )
        called = {"analyze": False}
        def forbidden_analysis(*args, **kwargs):
            called["analyze"] = True
            raise AssertionError("analysis must remain blocked while emergency stop is latched")
        monkeypatch.setattr(core_service, "analyze", forbidden_analysis)
        blocked = service.run_cycle(db, symbol="BTC/USDT", execute_shadow=True)
        assert blocked["error"] == "emergency_stop_active"
        assert blocked["executed"] is False
        assert called["analyze"] is False
    finally:
        db.close()


def test_analysis_engine_failure_latches_independent_emergency_stop(tmp_path, monkeypatch):
    db = _db(tmp_path)
    try:
        monkeypatch.setattr(
            core_service.market_data, "get_public_multi_timeframe",
            lambda symbol: core_service.market_data.get_multi_timeframe(symbol, seed=42),
        )
        monkeypatch.setattr(
            core_service, "analyze",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("simulated analysis failure")),
        )
        result = TradingOrchestrator().run_cycle(db, symbol="BTC/USDT", execute_shadow=True)
        assert result["error"] == "analysis_engine_error"
        assert result["emergency_stop_latched"] is True
        from app.models.entities import SystemState
        state = db.get(SystemState, 1)
        assert state.emergency_stop is True
        assert state.bot_run_state == "stopped"
        assert state.autonomous_enabled is False
    finally:
        db.close()



def test_orchestrator_blocks_stale_public_candle_and_latches_no_trade(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from app.core.enums import Timeframe

    db = _db(tmp_path)
    try:
        batches = core_service.market_data.get_multi_timeframe("BTC/USDT", seed=42)
        stale = batches[Timeframe.M5].df.rows[-1]
        stale["close_time"] = int(datetime.now(timezone.utc).timestamp() * 1000) - 3600_000
        monkeypatch.setattr(core_service.market_data, "get_public_multi_timeframe", lambda symbol: batches)
        result = TradingOrchestrator().run_cycle(
            db, symbol="BTC/USDT", execute_shadow=True, max_data_age_seconds=300,
        )
        assert result["ok"] is False
        assert result["error"] == "stale_or_future_market_data"
        assert result["executed"] is False
        assert ShadowTradingService().get_portfolio(db, env="demo", market="spot")["orders"] == []
    finally:
        db.close()



def test_engine_control_does_not_fake_runtime_state_changes(tmp_path):
    db = _db(tmp_path)
    try:
        core_service.ensure_bootstrap(db)
        before = {row["key"]: row for row in core_service.engines(db)}
        key = "risk_management"
        original_status = before[key]["status"]

        started = core_service.control_engine(db, key, "start")
        tested = core_service.control_engine(db, key, "test")
        after = {row["key"]: row for row in core_service.engines(db)}

        assert started["ok"] is False
        assert started["error"] == "engine_lifecycle_not_implemented"
        assert tested["ok"] is False
        assert tested["error"] == "engine_self_test_not_implemented"
        assert after[key]["status"] == original_status
    finally:
        db.close()


def test_worker_master_stop_during_analysis_blocks_shadow_side_effect(tmp_path, monkeypatch):
    from app.models.entities import SystemState

    db = _db(tmp_path)
    try:
        core_service.ensure_bootstrap(db)
        state = db.get(SystemState, 1)
        state.bot_run_state = "running"
        db.add(state)
        db.commit()
        monkeypatch.setattr(
            core_service.market_data, "get_public_multi_timeframe",
            lambda symbol: core_service.market_data.get_multi_timeframe(symbol, seed=42),
        )

        def stop_during_analysis(*args, **kwargs):
            current = db.get(SystemState, 1)
            current.bot_run_state = "stopped"
            current.bot_stop_reason = "user_stop"
            db.add(current)
            db.commit()
            return {
                "action": "buy", "decision_id": 999, "entry_price": 100.0,
                "risk": {"ok": True, "quantity_estimate": 0.1},
            }

        monkeypatch.setattr(core_service, "analyze", stop_during_analysis)
        result = TradingOrchestrator().run_cycle(
            db, symbol="BTC/USDT", execute_shadow=True, require_active_worker=True,
        )
        assert result["ok"] is False
        assert result["error"] == "master_stop_requested"
        assert result["executed"] is False
        assert ShadowTradingService().get_portfolio(db, env="demo", market="spot")["orders"] == []
    finally:
        db.close()

def test_core_analysis_uses_portfolio_exposure_cap_not_leverage_and_fails_closed(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.models.entities import SystemState

    db = _db(tmp_path)
    try:
        core_service.ensure_bootstrap(db)
        state = db.get(SystemState, 1)
        state.risk_limits = {"max_open_positions_cap": "10"}
        db.add(state)
        db.commit()

        captured = {}
        monkeypatch.setattr(
            core_service.technical, "analyze",
            lambda *args, **kwargs: SimpleNamespace(indicators={}),
        )
        def fake_decide(**kwargs):
            captured.update(kwargs)
            return {"action": "wait", "reason": "test", "confidence": 0.0}
        monkeypatch.setattr(core_service.decision, "decide", fake_decide)

        result = core_service.analyze(
            db, symbol="BTC/USDT", market_type="futures",
            environment_mode="demo", balance=1000.0,
            max_leverage=10.0, risk_per_trade=0.005,
            data_source="synthetic",
        )
        assert result["action"] == "wait"
        assert captured["max_exposure"] == 2000.0
        assert captured["max_open_positions"] == 0
    finally:
        db.close()


def test_orchestrator_fails_closed_on_malformed_demo_risk_limits(tmp_path, monkeypatch):
    from app.models.entities import SystemState

    db = _db(tmp_path)
    try:
        core_service.ensure_bootstrap(db)
        state = db.get(SystemState, 1)
        state.risk_limits = []
        db.add(state)
        db.commit()
        monkeypatch.setattr(
            core_service.market_data, "get_public_multi_timeframe",
            lambda symbol: core_service.market_data.get_multi_timeframe(symbol, seed=42),
        )
        result = TradingOrchestrator().run_cycle(
            db, symbol="BTC/USDT", execute_shadow=True,
        )
        assert result["ok"] is False
        assert result["error"] == "invalid_demo_risk_configuration"
        assert result["executed"] is False
        assert ShadowTradingService().get_portfolio(
            db, env="demo", market="spot",
        )["orders"] == []
    finally:
        db.close()



def test_orchestrator_executes_risk_sized_demo_futures_long_with_protection(tmp_path, monkeypatch):
    from app.core.config import settings

    db = _db(tmp_path)
    _select_futures_market(db)
    try:
        batches = core_service.market_data.get_multi_timeframe(
            "BTC/USDT", seed=42, market_type="futures",
        )
        batches[next(tf for tf in batches if tf.value == "5m")].df.rows[-1]["close_time"] = __import__("time").time() * 1000
        monkeypatch.setattr(
            core_service.market_data, "get_public_multi_timeframe",
            lambda symbol, market_type="spot": batches if market_type == "futures" else None,
        )
        monkeypatch.setattr(core_service, "_validate_public_market_batches", lambda *a, **k: None)
        monkeypatch.setattr(core_service.market_data, "get_public_mark_price", lambda symbol: {
            "symbol": "BTCUSDT", "mark_price": 100.0, "index_price": 100.0,
            "last_funding_rate": 0.0001,
            "time": __import__("time").time() * 1000,
            "source": "binance_usdm_futures_public_read_only",
        })
        monkeypatch.setattr(core_service, "analyze", lambda *a, **k: {
            "action": "long", "decision_id": 9001, "entry_price": 100.0,
            "stop_loss": 95.0, "take_profit": 110.0, "leverage": 2.0,
            "risk": {"ok": True, "quantity_estimate": 0.2},
        })
        result = TradingOrchestrator().run_cycle(
            db, symbol="BTC/USDT", market="futures", execute_shadow=True,
        )
        db.commit()
        assert result["executed"] is True
        assert result["execution_mode"] == "shadow_safe"
        assert result["market"] == "futures"
        assert result["mark_price"] == 100.0
        portfolio = ShadowTradingService().get_portfolio(db, env="demo", market="futures")
        assert len(portfolio["orders"]) == 1
        assert portfolio["orders"][0]["side"] == "buy"
        assert portfolio["positions"][0]["side"] == "long"
        assert portfolio["positions"][0]["quantity"] == 0.2
        assert portfolio["positions"][0]["shadow"] is True
        assert portfolio["positions"][0]["status"] == "open"
    finally:
        db.close()


def test_orchestrator_futures_short_uses_sell_side_and_short_position(tmp_path, monkeypatch):
    db = _db(tmp_path)
    _select_futures_market(db)
    try:
        batches = core_service.market_data.get_multi_timeframe(
            "ETH/USDT", seed=43, market_type="futures",
        )
        batches[next(tf for tf in batches if tf.value == "5m")].df.rows[-1]["close_time"] = __import__("time").time() * 1000
        monkeypatch.setattr(
            core_service.market_data, "get_public_multi_timeframe",
            lambda symbol, market_type="spot": batches,
        )
        monkeypatch.setattr(core_service, "_validate_public_market_batches", lambda *a, **k: None)
        monkeypatch.setattr(core_service.market_data, "get_public_mark_price", lambda symbol: {
            "symbol": "ETHUSDT", "mark_price": 100.0, "index_price": 100.0,
            "last_funding_rate": -0.0001,
            "time": __import__("time").time() * 1000,
            "source": "binance_usdm_futures_public_read_only",
        })
        monkeypatch.setattr(core_service, "analyze", lambda *a, **k: {
            "action": "short", "decision_id": 9002, "entry_price": 100.0,
            "stop_loss": 105.0, "take_profit": 90.0, "leverage": 3.0,
            "risk": {"ok": True, "quantity_estimate": 0.3},
        })
        result = TradingOrchestrator().run_cycle(
            db, symbol="ETH/USDT", market="futures", execute_shadow=True,
        )
        db.commit()
        assert result["executed"] is True
        portfolio = ShadowTradingService().get_portfolio(db, env="demo", market="futures")
        assert portfolio["orders"][0]["side"] == "sell"
        assert portfolio["positions"][0]["side"] == "short"
        assert portfolio["positions"][0]["leverage"] == 3.0
    finally:
        db.close()


def test_orchestrator_futures_missing_protection_never_opens_position(tmp_path, monkeypatch):
    db = _db(tmp_path)
    _select_futures_market(db)
    try:
        batches = core_service.market_data.get_multi_timeframe(
            "BTC/USDT", seed=44, market_type="futures",
        )
        batches[next(tf for tf in batches if tf.value == "5m")].df.rows[-1]["close_time"] = __import__("time").time() * 1000
        monkeypatch.setattr(
            core_service.market_data, "get_public_multi_timeframe",
            lambda symbol, market_type="spot": batches,
        )
        monkeypatch.setattr(core_service, "_validate_public_market_batches", lambda *a, **k: None)
        monkeypatch.setattr(core_service.market_data, "get_public_mark_price", lambda symbol: {
            "symbol": "BTCUSDT", "mark_price": 100.0, "index_price": 100.0,
            "last_funding_rate": 0.0,
            "time": __import__("time").time() * 1000,
            "source": "binance_usdm_futures_public_read_only",
        })
        monkeypatch.setattr(core_service, "analyze", lambda *a, **k: {
            "action": "long", "entry_price": 100.0, "leverage": 2.0,
            "risk": {"ok": True, "quantity_estimate": 0.2},
        })
        result = TradingOrchestrator().run_cycle(
            db, symbol="BTC/USDT", market="futures", execute_shadow=True,
        )
        assert result["executed"] is False
        assert result["execution_result"]["message"] == "futures_protection_or_notional_configuration_missing"
        assert ShadowTradingService().get_portfolio(db, env="demo", market="futures")["orders"] == []
    finally:
        db.close()


def test_orchestrator_futures_estimated_liquidation_closes_with_reduce_only(tmp_path, monkeypatch):
    import time

    db = _db(tmp_path)
    _select_futures_market(db)
    try:
        shadow = ShadowTradingService()
        opened = shadow.create_shadow_order(
            db, env="demo", market="futures", symbol="BTC/USDT", side="buy",
            order_type="market", quantity=0.2, price=100.0, reference_price=100.0,
            idempotency_key="futures-liquidation-entry-001", leverage=2.0,
            mark_price=100.0,
        )
        db.commit()
        assert opened.ok
        assert opened.position.metadata_json["margin_mode"] == "isolated"

        batches = core_service.market_data.get_multi_timeframe(
            "BTC/USDT", seed=45, market_type="futures",
        )
        batches[next(tf for tf in batches if tf.value == "5m")].df.rows[-1]["close_time"] = __import__("time").time() * 1000
        monkeypatch.setattr(
            core_service.market_data, "get_public_multi_timeframe",
            lambda symbol, market_type="spot": batches,
        )
        monkeypatch.setattr(core_service, "_validate_public_market_batches", lambda *a, **k: None)
        monkeypatch.setattr(core_service.market_data, "get_public_mark_price", lambda symbol: {
            "symbol": "BTCUSDT", "mark_price": 40.0, "index_price": 40.0,
            "last_funding_rate": 0.0, "time": time.time() * 1000,
            "source": "binance_usdm_futures_public_read_only",
        })
        result = TradingOrchestrator().run_cycle(
            db, symbol="BTC/USDT", market="futures", execute_shadow=True,
        )
        db.commit()
        assert result["executed"] is True
        assert result["execution_result"]["message"] == "futures_estimated_liquidation_exit"
        portfolio = shadow.get_portfolio(db, env="demo", market="futures")
        assert portfolio["snapshot"]["positions_open"] == 0
        assert portfolio["orders"][0]["side"] == "sell"
        assert portfolio["orders"][0]["status"] == "filled"
    finally:
        db.close()


def test_core_start_bot_accepts_only_selected_demo_futures_market(tmp_path):
    from app.models.entities import SystemState

    db = _db(tmp_path)
    try:
        core_service.ensure_bootstrap(db)
        state = db.get(SystemState, 1)
        state.market_type = "futures"
        db.add(state)
        db.commit()
        started = core_service.start_bot(db, account_target="demo")
        assert started["ok"] is True
        assert started["state"]["market_type"] == "futures"
        assert started["state"]["environment_mode"] == "demo"
    finally:
        db.close()


def test_orchestrator_settles_futures_funding_once_and_daily_guard_counts_it(tmp_path, monkeypatch):
    import time
    from datetime import datetime, timezone

    db = _db(tmp_path)
    _select_futures_market(db)
    try:
        shadow = ShadowTradingService()
        opened = shadow.create_shadow_order(
            db, env="demo", market="futures", symbol="BTC/USDT", side="buy",
            order_type="market", quantity=0.2, price=100.0, reference_price=100.0,
            idempotency_key="futures-funding-entry-001", leverage=2.0,
            mark_price=100.0,
        )
        db.commit()
        assert opened.ok
        now_ms = int(time.time() * 1000)
        metadata = dict(opened.position.metadata_json or {})
        metadata["funding_cursor_ms"] = now_ms - 100_000
        metadata["funding_daily_date"] = datetime.now(timezone.utc).date().isoformat()
        metadata["funding_daily_pnl"] = 0.0
        opened.position.metadata_json = metadata
        db.add(opened.position)
        db.commit()

        batches = core_service.market_data.get_multi_timeframe(
            "BTC/USDT", seed=46, market_type="futures",
        )
        batches[next(tf for tf in batches if tf.value == "5m")].df.rows[-1]["close_time"] = __import__("time").time() * 1000
        monkeypatch.setattr(
            core_service.market_data, "get_public_multi_timeframe",
            lambda symbol, market_type="spot": batches,
        )
        monkeypatch.setattr(core_service, "_validate_public_market_batches", lambda *a, **k: None)
        monkeypatch.setattr(core_service.market_data, "get_public_mark_price", lambda symbol: {
            "symbol": "BTCUSDT", "mark_price": 100.0, "index_price": 100.0,
            "last_funding_rate": 0.001, "time": time.time() * 1000,
            "source": "binance_usdm_futures_public_read_only",
        })
        funding_calls = []
        def fake_funding(symbol, *, start_time, end_time):
            funding_calls.append((symbol, start_time, end_time))
            if len(funding_calls) == 1:
                return [{"symbol": "BTCUSDT", "timestamp": now_ms - 1_000,
                         "rate": 0.001, "source": "binance_usdm_futures_public_read_only"}]
            return []
        monkeypatch.setattr(core_service.market_data, "get_public_funding_rates", fake_funding)
        monkeypatch.setattr(core_service, "analyze", lambda *a, **k: {
            "action": "wait", "decision_id": 9003,
        })

        result = TradingOrchestrator().run_cycle(
            db, symbol="BTC/USDT", market="futures", execute_shadow=True,
        )
        db.commit()
        assert result["executed"] is False
        assert len(result["funding_settlements"]) == 1
        assert result["funding_settlements"][0]["estimated_payment"] == -0.02
        position = shadow.get_portfolio(db, env="demo", market="futures")["positions"][0]
        assert position["realized_pnl"] == -0.02
        assert shadow._daily_realized_pnl(db, env="demo", market="futures") == -0.02

        second = TradingOrchestrator().run_cycle(
            db, symbol="BTC/USDT", market="futures", execute_shadow=True,
        )
        db.commit()
        assert second["funding_settlements"] == []
        assert len(funding_calls) == 2
    finally:
        db.close()


def test_orchestrator_caps_added_futures_quantity_against_total_position_notional(tmp_path, monkeypatch):
    from app.models.entities import SystemState

    db = _db(tmp_path)
    _select_futures_market(db)
    try:
        state = db.get(SystemState, 1)
        state.risk_limits = {"demo_initial_balance": 1000.0, "demo_risk_per_trade": 0.005}
        db.add(state)
        db.commit()
        shadow = ShadowTradingService()
        opened = shadow.create_shadow_order(
            db, env="demo", market="futures", symbol="BTC/USDT", side="buy",
            order_type="market", quantity=7.0, price=100.0, reference_price=100.0,
            idempotency_key="futures-cap-existing-700", leverage=10.0,
        )
        db.commit()
        assert opened.ok

        batches = core_service.market_data.get_multi_timeframe(
            "BTC/USDT", seed=47, market_type="futures",
        )
        batches[next(tf for tf in batches if tf.value == "5m")].df.rows[-1]["close_time"] = __import__("time").time() * 1000
        monkeypatch.setattr(
            core_service.market_data, "get_public_multi_timeframe",
            lambda symbol, market_type="spot": batches,
        )
        monkeypatch.setattr(core_service, "_validate_public_market_batches", lambda *a, **k: None)
        monkeypatch.setattr(core_service.market_data, "get_public_mark_price", lambda symbol: {
            "symbol": "BTCUSDT", "mark_price": 100.0, "index_price": 100.0,
            "last_funding_rate": 0.0, "time": __import__("time").time() * 1000,
            "source": "binance_usdm_futures_public_read_only",
        })
        monkeypatch.setattr(core_service, "analyze", lambda *a, **k: {
            "action": "long", "entry_price": 100.0,
            "stop_loss": 95.0, "take_profit": 110.0, "leverage": 10.0,
            "risk": {"ok": True, "quantity_estimate": 2.0},
        })
        result = TradingOrchestrator().run_cycle(
            db, symbol="BTC/USDT", market="futures", execute_shadow=True,
        )
        db.commit()
        assert result["executed"] is True
        portfolio = shadow.get_portfolio(db, env="demo", market="futures")
        assert portfolio["positions"][0]["quantity"] == 8.0
        assert portfolio["snapshot"]["exposure_notional"] == 800.0
        assert portfolio["orders"][0]["filled_quantity"] == 1.0
    finally:
        db.close()
