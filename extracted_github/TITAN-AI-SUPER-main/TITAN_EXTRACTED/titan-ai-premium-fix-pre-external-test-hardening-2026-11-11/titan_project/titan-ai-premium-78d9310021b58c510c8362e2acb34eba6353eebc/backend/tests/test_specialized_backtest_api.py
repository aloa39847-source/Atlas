import asyncio
import json

from app import star_app


def _json(response):
    return json.loads(response.body.decode("utf-8"))


def test_specialized_orderflow_backtest_api_is_authenticated_and_offline(monkeypatch):
    class FakeDB:
        def close(self): pass
        def rollback(self): pass

    events = [
        {"timestamp": 1000, "symbol": "BTCUSDT", "best_bid": 99, "best_ask": 100,
         "bid_depth_quote": 1000, "ask_depth_quote": 1000,
         "depth_imbalance": 0.2, "taker_flow_imbalance": 0.3},
        {"timestamp": 2000, "symbol": "BTCUSDT", "best_bid": 100, "best_ask": 101,
         "bid_depth_quote": 1000, "ask_depth_quote": 1000,
         "depth_imbalance": 0.3, "taker_flow_imbalance": 0.4},
        {"timestamp": 3000, "symbol": "BTCUSDT", "best_bid": 110, "best_ask": 111,
         "bid_depth_quote": 1000, "ask_depth_quote": 1000,
         "depth_imbalance": 0.1, "taker_flow_imbalance": 0.2},
    ]
    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))

    async def request_body(_request):
        return {
            "strategy_key": "liquidity_orderflow",
            "data_source": "user_supplied_historical",
            "events": events,
            "signals": [{"index": 0, "action": "buy"}, {"index": 1, "action": "sell"}],
            "initial_cash": 1000, "fee_rate": 0, "slippage_rate": 0,
            "max_notional": 80,
        }
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.specialized_backtest(None))
    result = _json(response)
    assert response.status_code == 200
    assert result["strategy_key"] == "liquidity_orderflow"
    assert result["data_source"] == "user_supplied_historical_orderflow"
    assert result["execution"] == "simulated_only"
    assert result["no_live_orders"] is True


def test_specialized_orderflow_api_rejects_unverified_dataset_marker(monkeypatch):
    class FakeDB:
        def close(self): pass
        def rollback(self): pass

    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))

    async def request_body(_request):
        return {
            "strategy_key": "liquidity_orderflow",
            "data_source": "live",
            "events": [{}, {}],
            "signals": [],
        }
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.specialized_backtest(None))
    assert response.status_code == 400
    assert _json(response)["error"] == "verified_historical_orderflow_dataset_required"


def test_specialized_backtest_api_rejects_unknown_strategy(monkeypatch):
    class FakeDB:
        def close(self): pass
        def rollback(self): pass

    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))

    async def request_body(_request):
        return {"strategy_key": "scalping_m5"}
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.specialized_backtest(None))
    assert response.status_code == 400
    assert _json(response)["error"] == "specialized_strategy_must_be_orderflow_or_arbitrage"
