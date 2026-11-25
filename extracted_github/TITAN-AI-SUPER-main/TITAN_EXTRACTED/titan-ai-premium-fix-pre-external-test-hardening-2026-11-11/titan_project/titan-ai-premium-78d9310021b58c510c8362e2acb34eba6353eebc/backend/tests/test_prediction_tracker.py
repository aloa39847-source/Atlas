from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import db as dbmod
from app.models.entities import DecisionRecord, LearningRecord
from app.services.prediction_tracker import PredictionTrackerService


class FakeMarketData:
    def __init__(self, prices):
        self.prices = prices
        self.requested = None
        self.requested_by_market = {}

    def get_public_last_prices(self, symbols, market_type="spot"):
        self.requested = set(symbols)
        self.requested_by_market[market_type] = set(symbols)
        return self.prices


def _db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'prediction_tracker.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    monkeypatch.setattr(dbmod, "engine", engine)
    dbmod.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return factory(), engine


def test_prediction_tracker_evaluates_matured_spot_buy_once(tmp_path, monkeypatch):
    db, engine = _db(tmp_path, monkeypatch)
    try:
        decision = DecisionRecord(
            environment_mode="demo", market_type="spot", symbol="BTC/USDT",
            action="buy", entry_price=100.0, rationale="test",
            data_source="binance_public",
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db.add(decision); db.commit(); db.refresh(decision)
        market = FakeMarketData({"BTC/USDT": 110.0})
        tracker = PredictionTrackerService(market)

        result = tracker.evaluate_matured(db, horizon_seconds=3600)
        assert result["ok"] is True
        assert result["evaluated"] == 1
        assert result["items"][0]["outcome"] == "favorable"
        assert result["items"][0]["directional_return_percent"] == 10.0
        assert market.requested == {"BTC/USDT"}
        assert market.requested_by_market == {"spot": {"BTC/USDT"}}
        stored = db.query(LearningRecord).filter_by(
            category="prediction_outcome", subject=str(decision.id),
        ).one()
        assert stored.verified is False
        again = tracker.evaluate_matured(db, horizon_seconds=3600)
        assert again["evaluated"] == 0
    finally:
        db.close(); engine.dispose()


def test_prediction_tracker_never_treats_spot_sell_as_short_and_supports_futures_short(tmp_path, monkeypatch):
    db, engine = _db(tmp_path, monkeypatch)
    try:
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        spot_sell = DecisionRecord(
            environment_mode="demo", market_type="spot", symbol="BTC/USDT",
            action="sell", entry_price=100.0, rationale="spot exit",
            data_source="binance_public", created_at=old,
        )
        futures_short = DecisionRecord(
            environment_mode="demo", market_type="futures", symbol="ETH/USDT",
            action="short", entry_price=100.0, rationale="short",
            data_source="binance_public", created_at=old,
        )
        db.add_all([spot_sell, futures_short]); db.commit()
        market = FakeMarketData({"BTC/USDT": 90.0, "ETH/USDT": 90.0})
        result = PredictionTrackerService(market).evaluate_matured(db, horizon_seconds=3600)
        assert result["evaluated"] == 1
        assert market.requested_by_market == {"futures": {"ETH/USDT"}}
        assert result["items"][0]["symbol"] == "ETH/USDT"
        assert result["items"][0]["outcome"] == "favorable"
        assert any(item["reason"] == "spot_sell_is_not_a_short_prediction" for item in result["skipped"])
    finally:
        db.close(); engine.dispose()


def test_prediction_tracker_skips_unmatured_and_rejects_invalid_horizon(tmp_path, monkeypatch):
    db, engine = _db(tmp_path, monkeypatch)
    try:
        db.add(DecisionRecord(
            environment_mode="demo", market_type="spot", symbol="BTC/USDT",
            action="buy", entry_price=100.0, rationale="recent",
            data_source="binance_public", created_at=datetime.now(timezone.utc),
        ))
        db.commit()
        tracker = PredictionTrackerService(FakeMarketData({"BTC/USDT": 110.0}))
        result = tracker.evaluate_matured(db, horizon_seconds=3600)
        assert result["evaluated"] == 0
        assert result["skipped"][0]["reason"] == "prediction_horizon_not_matured"
        assert tracker.evaluate_matured(db, horizon_seconds=1)["error"] == "horizon_seconds_out_of_range"
    finally:
        db.close(); engine.dispose()


def test_prediction_tracker_uses_each_decisions_direction_in_mixed_batch(tmp_path, monkeypatch):
    db, engine = _db(tmp_path, monkeypatch)
    try:
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        buy = DecisionRecord(
            environment_mode="demo", market_type="spot", symbol="BTC/USDT",
            action="buy", entry_price=100.0, rationale="long direction",
            data_source="binance_public", created_at=old,
        )
        short = DecisionRecord(
            environment_mode="demo", market_type="futures", symbol="ETH/USDT",
            action="short", entry_price=100.0, rationale="short direction",
            data_source="binance_public", created_at=old,
        )
        db.add_all([buy, short]); db.commit(); db.refresh(buy); db.refresh(short)
        tracker = PredictionTrackerService(FakeMarketData({
            "BTC/USDT": 110.0,
            "ETH/USDT": 110.0,
        }))
        result = tracker.evaluate_matured(db, horizon_seconds=3600)
        assert result["evaluated"] == 2
        by_symbol = {item["symbol"]: item for item in result["items"]}
        assert by_symbol["BTC/USDT"]["outcome"] == "favorable"
        assert by_symbol["BTC/USDT"]["directional_return_percent"] == 10.0
        assert by_symbol["ETH/USDT"]["outcome"] == "unfavorable"
        assert by_symbol["ETH/USDT"]["directional_return_percent"] == -10.0
        stored = {
            row.subject: row.evidence["action"]
            for row in db.query(LearningRecord).filter_by(category="prediction_outcome").all()
        }
        assert stored[str(buy.id)] == "buy"
        assert stored[str(short.id)] == "short"
        assert tracker.market_data.requested_by_market == {
            "spot": {"BTC/USDT"}, "futures": {"ETH/USDT"},
        }
    finally:
        db.close(); engine.dispose()
