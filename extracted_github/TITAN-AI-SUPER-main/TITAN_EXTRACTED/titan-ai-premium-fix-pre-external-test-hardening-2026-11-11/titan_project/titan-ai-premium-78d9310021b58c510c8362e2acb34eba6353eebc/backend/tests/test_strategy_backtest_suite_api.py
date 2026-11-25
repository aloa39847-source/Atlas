import asyncio
import json
import time
from types import SimpleNamespace

from app import star_app
from app.engines.market_data import Candles


def _rows(count=120):
    rows = []
    previous = 100.0
    end = int(time.time() * 1000) - 60_000
    start = end - count * 3_600_000
    for index in range(count):
        close = previous + 0.35
        stamp = start + index * 3_600_000
        rows.append({
            "timestamp": stamp, "close_time": stamp + 3_600_000 - 1,
            "open": previous, "high": close + 0.4,
            "low": previous - 0.4, "close": close, "volume": 1000.0,
        })
        previous = close
    rows[-1]["close_time"] = end
    return rows


def _json(response):
    return json.loads(response.body.decode("utf-8"))


def test_compare_backtest_api_uses_public_spot_history_and_simulates_only(monkeypatch):
    class FakeDB:
        def close(self): pass
        def rollback(self): pass

    rows = _rows()
    batch = SimpleNamespace(df=Candles(rows), source=SimpleNamespace(value="live_public"), market_type="spot")
    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    monkeypatch.setattr(
        star_app.core_service.market_data, "get_public_candles",
        lambda symbol, timeframe, limit, market_type: batch,
    )

    async def request_body(_request):
        return {
            "symbol": "BTC/USDT", "timeframe": "1h", "market_type": "spot",
            "strategy_keys": ["trend_following_ema"], "initial_cash": 150,
            "fee_rate": 0, "slippage_rate": 0,
        }
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.compare_strategy_backtests(None))
    result = _json(response)
    assert response.status_code == 200
    assert result["strategies_tested"] == 1
    assert result["leaderboard"][0]["strategy_key"] == "trend_following_ema"
    assert result["execution"] == "simulated_only"
    assert result["no_live_orders"] is True
