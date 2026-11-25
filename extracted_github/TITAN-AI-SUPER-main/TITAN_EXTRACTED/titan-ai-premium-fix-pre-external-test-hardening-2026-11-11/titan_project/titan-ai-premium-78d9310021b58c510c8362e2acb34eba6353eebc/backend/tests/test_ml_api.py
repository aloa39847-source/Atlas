import asyncio
import json
import math
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.enums import Timeframe
from app.engines.hybrid.ml_engine import ml_engine
from app.engines.market_data import Candles
from app.models import db as dbmod
from app.models.entities import LearningRecord
from app import star_app


def _candles(count=120):
    rows = []
    previous = 100.0
    for i in range(count):
        close = 100.0 + i * 0.05 + math.sin(i * 0.31)
        open_price = previous
        rows.append({
            "open": open_price,
            "high": max(open_price, close) * 1.01,
            "low": min(open_price, close) * 0.99,
            "close": close,
            "volume": 1000 + 100 * (1 + math.sin(i * 0.17)),
            "timestamp": i * 3_600_000,
            "close_time": i * 3_600_000 + 3_599_999,
        })
        previous = close
    return rows


def _response_json(response):
    return json.loads(response.body.decode("utf-8"))


def test_ml_api_trains_persists_and_predicts_without_exchange_execution(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'ml_api.db'}", future=True)
    dbmod.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(star_app, "db_sess", factory)
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    rows = _candles()
    batch = SimpleNamespace(df=Candles(rows), source=SimpleNamespace(value="live_public"))
    monkeypatch.setattr(star_app, "_ml_timeframe_batch", lambda symbol, timeframe: (Timeframe.H1, batch))
    model_key = "BTC/USDT|1h"
    ml_engine.models.pop(model_key, None)
    original_body = star_app.body

    async def body_train(_request):
        return {"symbol": "BTC/USDT", "timeframe": "1h", "epochs": 100}

    monkeypatch.setattr(star_app, "body", body_train)
    try:
        trained = asyncio.run(star_app.ml_train(None))
        data = _response_json(trained)
        assert trained.status_code == 200
        assert data["ok"] is True
        assert data["model_key"] == model_key
        assert data["data_source"] == "binance_public_read_only"

        async def body_predict(_request):
            return {"symbol": "BTC/USDT", "timeframe": "1h"}

        monkeypatch.setattr(star_app, "body", body_predict)
        predicted = asyncio.run(star_app.ml_predict(None))
        prediction = _response_json(predicted)
        assert predicted.status_code == 200
        assert prediction["ok"] is True
        assert prediction["execution"] == "none"

        session = factory()
        try:
            saved = session.query(LearningRecord).filter_by(category="ml_model", subject=model_key).one()
            assert saved.evidence["model"]["model_key"] == model_key
        finally:
            session.close()
    finally:
        ml_engine.models.pop(model_key, None)
        ml_engine.model = None
        engine.dispose()
