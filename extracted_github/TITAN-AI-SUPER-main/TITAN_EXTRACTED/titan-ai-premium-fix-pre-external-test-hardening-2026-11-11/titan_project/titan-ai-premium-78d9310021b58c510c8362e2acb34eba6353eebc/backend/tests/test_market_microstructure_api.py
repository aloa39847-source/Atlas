import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import star_app
from app.models import db as dbmod
from app.models.entities import EngineState
from app.services.core_service import core_service


def test_bootstrap_refreshes_partial_engine_maturity_statuses(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'engine_registry.db'}", future=True)
    dbmod.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = factory()
    try:
        db.add(EngineState(engine_key="backtesting", name="Backtesting Engine", status="not_implemented"))
        db.add(EngineState(engine_key="liquidity_analysis", name="Liquidity Analysis Engine", status="needs_external_service"))
        db.commit()
        core_service.ensure_bootstrap(db)
        assert db.get(EngineState, 1) is not None
        backtest = db.query(EngineState).filter_by(engine_key="backtesting").one()
        liquidity = db.query(EngineState).filter_by(engine_key="liquidity_analysis").one()
        order_flow = db.query(EngineState).filter_by(engine_key="order_flow").one()
        assert backtest.status == "implemented_partial"
        assert liquidity.status == "implemented_partial"
        assert order_flow.status == "implemented_partial"
    finally:
        db.close()
        engine.dispose()


def test_market_microstructure_api_returns_read_only_public_metrics(monkeypatch):
    class FakeDB:
        def close(self): pass

    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    monkeypatch.setattr(
        "app.engines.liquidity.LiquidityAnalysisEngine.fetch_and_analyze",
        lambda self, symbol, limit, market_type="spot": {"ok": True, "symbol": symbol, "market_type": market_type, "spread_percent": 0.02},
    )
    monkeypatch.setattr(
        "app.engines.order_flow.OrderFlowEngine.fetch_and_analyze",
        lambda self, symbol, limit, market_type="spot": {"ok": True, "symbol": symbol, "market_type": market_type, "taker_flow_imbalance": 0.1},
    )
    request = SimpleNamespace(query_params={"symbol": "BTC/USDT"})
    response = asyncio.run(star_app.market_microstructure(request))
    data = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert data["ok"] is True
    assert data["source"] == "binance_public_read_only"
    assert data["execution"] == "none"


def test_market_microstructure_api_selects_futures_public_market(monkeypatch):
    class FakeDB:
        def close(self): pass

    seen = []
    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: ({"id": 1}, None))
    monkeypatch.setattr(
        "app.engines.liquidity.LiquidityAnalysisEngine.fetch_and_analyze",
        lambda self, symbol, limit, market_type="spot": seen.append(("liquidity", market_type)) or {"ok": True, "symbol": symbol},
    )
    monkeypatch.setattr(
        "app.engines.order_flow.OrderFlowEngine.fetch_and_analyze",
        lambda self, symbol, limit, market_type="spot": seen.append(("order_flow", market_type)) or {"ok": True, "symbol": symbol},
    )
    request = SimpleNamespace(query_params={"symbol": "BTC/USDT", "market_type": "futures"})
    response = asyncio.run(star_app.market_microstructure(request))
    data = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert data["market_type"] == "futures"
    assert data["source"] == "binance_futures_public_read_only"
    assert seen == [("liquidity", "futures"), ("order_flow", "futures")]



def test_trade_execution_registry_reports_supervised_service_and_no_fake_controls(tmp_path):
    from app.services.core_service import ENGINE_REGISTRY

    engine = create_engine(f"sqlite:///{tmp_path / 'execution_engine_registry.db'}", future=True)
    dbmod.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = factory()
    try:
        core_service.ensure_bootstrap(db)
        registered = {key: status for key, _name, status in ENGINE_REGISTRY}
        assert registered["trade_execution"] == "implemented_partial"
        row = next(item for item in core_service.get_engines_detailed(db) if item["key"] == "trade_execution")
        assert row["can_start_stop"] is False
        assert row["controls"] == []
        assert row["details"]["lifecycle_control"] == "managed_by_trading_supervisor"
        assert "supervised worker" in row["message"]
    finally:
        db.close()
        engine.dispose()


def test_fresh_system_state_preserves_conservative_wallet_leverage_cap(tmp_path):
    from app.models.entities import SystemState

    engine = create_engine(f"sqlite:///{tmp_path / 'default_leverage_cap.db'}", future=True)
    dbmod.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = factory()
    try:
        core_service.ensure_bootstrap(db)
        state = db.get(SystemState, 1)
        assert state.risk_limits["max_leverage_cap"] == 1.0
    finally:
        db.close()
        engine.dispose()


def test_shared_ml_batch_validator_rejects_boolean_and_non_finite_timestamps():
    import math
    import pytest
    from app.core.enums import Timeframe

    class FakeCandles:
        def __init__(self, rows):
            self.rows = rows

        def __len__(self):
            return len(self.rows)

    for stamp in (True, False, math.nan, math.inf, 0):
        batch = SimpleNamespace(
            source=SimpleNamespace(value="live_public"),
            df=FakeCandles([{"close_time": stamp}] * 50),
        )
        with pytest.raises(ValueError, match="public_candle_timestamp_required"):
            star_app._validate_ml_public_batch(Timeframe.H1, batch)


def test_engine_integration_map_covers_registry_and_preserves_authority_boundaries(tmp_path):
    from app.services.core_service import ENGINE_INTEGRATION_MAP, ENGINE_REGISTRY

    registered = {key for key, _name, _status in ENGINE_REGISTRY}
    assert set(ENGINE_INTEGRATION_MAP) == registered
    assert len(ENGINE_INTEGRATION_MAP) == 25

    # Only explicitly mapped analytical inputs enter the deterministic decision
    # evidence path. Execution, discovery, research and reporting remain separate.
    assert ENGINE_INTEGRATION_MAP["technical_analysis"]["decision_evidence"] is True
    assert ENGINE_INTEGRATION_MAP["news_analysis"]["decision_evidence"] is True
    assert ENGINE_INTEGRATION_MAP["risk_management"]["authority"] == "independent_veto"
    assert ENGINE_INTEGRATION_MAP["trade_execution"]["authority"] == "testnet_worker_only_live_hard_disabled"
    assert ENGINE_INTEGRATION_MAP["arbitrage_analysis"]["authority"] == "estimate_only_no_execution"
    assert ENGINE_INTEGRATION_MAP["self_evolution"]["authority"] == "explicit_approval_required"
    assert ENGINE_INTEGRATION_MAP["fundamental_analysis"]["decision_evidence"] is False


def test_detailed_engine_status_exposes_verified_integration_scope(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'engine_integration_details.db'}", future=True)
    dbmod.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = factory()
    try:
        core_service.ensure_bootstrap(db)
        rows = {item["key"]: item for item in core_service.get_engines_detailed(db)}
        assert len(rows) == 25
        assert rows["technical_analysis"]["details"]["integration"]["mode"] == "decision_pipeline"
        assert rows["fundamental_analysis"]["details"]["integration"]["mode"] == "on_demand"
        assert rows["trade_execution"]["details"]["integration"]["authority"] == "testnet_worker_only_live_hard_disabled"
        assert rows["risk_management"]["details"]["integration"]["authority"] == "independent_veto"
    finally:
        db.close()
        engine.dispose()
