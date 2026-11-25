from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import db as dbmod
from app.services.performance_analytics import PerformanceAnalyticsService
from app.services.shadow_trading import ShadowTradingService


def _db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'performance.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    monkeypatch.setattr(dbmod, "engine", engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    dbmod.Base.metadata.create_all(bind=engine)
    return factory(), engine


def test_performance_analytics_uses_shadow_ledger_and_exit_fill_pnl(tmp_path, monkeypatch):
    db, engine = _db(tmp_path, monkeypatch)
    try:
        service = ShadowTradingService()
        buy = service.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.1, price=100, reference_price=100,
            idempotency_key="performance-buy-01", fee_rate=0.001,
        )
        db.commit()
        assert buy.ok
        sell = service.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="sell",
            order_type="limit", quantity=0.1, price=110, reference_price=110,
            idempotency_key="performance-sell-01", fee_rate=0.001,
        )
        db.commit()
        assert sell.ok

        result = PerformanceAnalyticsService().evaluate(db, environment_mode="demo", market_type="spot")
        assert result["ok"] is True
        assert result["scope"]["shadow_only"] is True
        assert result["realized_exit_fills"] == 1
        assert result["wins"] == 1
        assert result["win_rate"] == 1.0
        assert result["realized_pnl_net"] > 0
        assert result["total_fees_paid"] > 0
        assert result["open_positions"] == []
        assert result["data_scope"] == "internal_demo_shadow_records_only"
    finally:
        db.close()
        engine.dispose()


def test_performance_analytics_handles_empty_shadow_ledger_and_invalid_scope(tmp_path, monkeypatch):
    db, engine = _db(tmp_path, monkeypatch)
    try:
        result = PerformanceAnalyticsService().evaluate(db)
        assert result["ok"] is True
        assert result["initial_cash"] == 150.0
        assert result["realized_exit_fills"] == 0
        assert result["win_rate"] == 0.0
        assert result["max_drawdown_from_snapshots"] is None
        assert PerformanceAnalyticsService().evaluate(db, environment_mode="unknown")["ok"] is False
    finally:
        db.close()
        engine.dispose()


def test_performance_analytics_counts_long_and_short_futures_reduce_only_exits(tmp_path, monkeypatch):
    db, engine = _db(tmp_path, monkeypatch)
    try:
        service = ShadowTradingService()
        long_entry = service.create_shadow_order(
            db, env="demo", market="futures", symbol="BTC/USDT", side="buy",
            order_type="market", quantity=0.01, price=100, reference_price=100,
            idempotency_key="performance-futures-long-entry", leverage=2,
        )
        short_entry = service.create_shadow_order(
            db, env="demo", market="futures", symbol="ETH/USDT", side="sell",
            order_type="market", quantity=0.01, price=100, reference_price=100,
            idempotency_key="performance-futures-short-entry", leverage=2,
        )
        db.commit()
        assert long_entry.ok and short_entry.ok
        assert service.close_shadow_position(db, position_id=long_entry.position.id, close_price=110)["ok"]
        assert service.close_shadow_position(db, position_id=short_entry.position.id, close_price=90)["ok"]
        db.commit()

        result = PerformanceAnalyticsService().evaluate(db, environment_mode="demo", market_type="futures")
        assert result["ok"] is True
        assert result["realized_exit_fills"] == 2
        assert result["wins"] == 2
        assert result["win_rate"] == 1.0
        assert result["realized_pnl_net"] > 0
        assert result["open_positions"] == []
    finally:
        db.close()
        engine.dispose()
