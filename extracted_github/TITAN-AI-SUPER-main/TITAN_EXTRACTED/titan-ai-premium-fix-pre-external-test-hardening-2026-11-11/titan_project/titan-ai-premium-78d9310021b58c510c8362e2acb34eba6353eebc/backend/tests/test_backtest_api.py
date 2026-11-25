import asyncio
import json
import time
from types import SimpleNamespace

from app import star_app
from app.engines.market_data import Candles
from app.core.enums import Timeframe


def _rows(count=120):
    rows = []
    previous = 100.0
    for i in range(count):
        close = 100.0 + i * 0.3 + (0.1 if i % 2 else -0.1)
        open_price = previous
        rows.append({
            "open": open_price,
            "high": max(open_price, close) + 0.5,
            "low": min(open_price, close) - 0.5,
            "close": close,
            "volume": 1000.0 + i,
            "timestamp": int(time.time() * 1000) - (count - i) * 3_600_000,
            "close_time": int(time.time() * 1000) - 60_000,
        })
        previous = close
    return rows


def _json(response):
    return json.loads(response.body.decode("utf-8"))


def test_backtest_api_runs_historical_and_walk_forward_with_public_candles(monkeypatch):
    class FakeDB:
        def close(self): pass

    rows = _rows()
    batch = SimpleNamespace(df=Candles(rows), source=SimpleNamespace(value="live_public"))
    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    monkeypatch.setattr(star_app, "body", lambda request: None)

    async def historical_body(_request):
        return {"symbol": "BTC/USDT", "timeframe": "1h", "strategy_key": "trend_following_ema", "mode": "historical", "limit": 120}
    monkeypatch.setattr(star_app, "body", historical_body)
    monkeypatch.setattr(star_app.core_service.market_data, "get_public_candles", lambda symbol, timeframe, limit: batch)

    historical = asyncio.run(star_app.backtest_strategy(None))
    data = _json(historical)
    assert historical.status_code == 200
    assert data["symbol"] == "BTC/USDT"
    assert data["data_source"] == "binance_public_read_only"
    assert data["execution"] == "simulated_only"
    assert data["no_lookahead"] is True

    async def walk_forward_body(_request):
        return {
            "symbol": "BTC/USDT", "timeframe": "1h",
            "strategy_key": "trend_following_ema", "mode": "walk_forward",
            "limit": 120, "train_window": 60, "test_window": 30, "step": 30,
        }
    monkeypatch.setattr(star_app, "body", walk_forward_body)
    walked = asyncio.run(star_app.backtest_strategy(None))
    result = _json(walked)
    assert walked.status_code == 200
    assert result["out_of_sample_ready"] is True
    assert result["fold_count"] == 2
    assert result["no_lookahead"] is True


def test_futures_backtest_api_uses_public_futures_candles_and_funding(monkeypatch):
    class FakeDB:
        def close(self): pass

    rows = _rows(120)
    batch = SimpleNamespace(
        df=Candles(rows), source=SimpleNamespace(value="live_public"),
        market_type="futures",
    )
    calls = {}
    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))

    async def request_body(_request):
        return {
            "symbol": "BTC/USDT", "timeframe": "1h",
            "strategy_key": "trend_following_ema", "limit": 120,
            "initial_cash": 1000, "leverage": 2, "notional": 400,
        }
    monkeypatch.setattr(star_app, "body", request_body)

    def get_candles(symbol, timeframe, limit, market_type):
        calls["market_type"] = market_type
        return batch

    def get_funding(symbol, *, start_time, end_time):
        calls["funding_range"] = (start_time, end_time)
        return []

    monkeypatch.setattr(star_app.core_service.market_data, "get_public_candles", get_candles)
    monkeypatch.setattr(star_app.core_service.market_data, "get_public_funding_rates", get_funding)
    response = asyncio.run(star_app.futures_backtest(None))
    result = _json(response)
    assert response.status_code == 200
    assert calls["market_type"] == "futures"
    assert calls["funding_range"][1] >= calls["funding_range"][0]
    assert result["data_source"] == "binance_usdm_futures_public_read_only"
    assert result["execution"] == "simulated_only"
    assert result["strategy_key"] == "trend_following_ema"
    assert result["no_lookahead"] is True


def test_futures_backtest_api_rejects_invalid_limits_before_market_access(monkeypatch):
    class FakeDB:
        def close(self): pass

    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))

    async def request_body(_request):
        return {"symbol": "BTC/USDT", "timeframe": "1h", "limit": True}
    monkeypatch.setattr(star_app, "body", request_body)

    def unexpected(*args, **kwargs):
        raise AssertionError("market data must not be accessed for invalid limit")
    monkeypatch.setattr(star_app.core_service.market_data, "get_public_candles", unexpected)
    response = asyncio.run(star_app.futures_backtest(None))
    assert response.status_code == 400
    assert _json(response)["error"] == "candle_limit_must_be_60_to_500"


def test_arbitrage_api_is_authenticated_and_read_only(monkeypatch):
    class FakeDB:
        def close(self): pass

    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    async def request_body(_request):
        return {
            "route": ["USDT", "BTC", "ETH", "USDT"],
            "starting_amount": 100,
        }
    monkeypatch.setattr(star_app, "body", request_body)

    class FakeArbitrage:
        def __init__(self, market_data):
            assert market_data is star_app.core_service.market_data
        def fetch_and_analyze(self, route, **kwargs):
            assert route == ["USDT", "BTC", "ETH", "USDT"]
            assert kwargs["starting_amount"] == 100
            return {"ok": True, "candidate": False, "profit_percent_estimate": -0.2}

    monkeypatch.setattr("app.engines.arbitrage.ArbitrageAnalysisEngine", FakeArbitrage)
    response = asyncio.run(star_app.arbitrage_analysis(None))
    result = _json(response)
    assert response.status_code == 200
    assert result["execution"] == "none"
    assert result["data_access"] == "binance_public_read_only"


def test_arbitrage_api_rejects_invalid_route_and_boolean_amount(monkeypatch):
    class FakeDB:
        def close(self): pass

    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    async def invalid_route(_request):
        return {"route": ["USDT", "BTC"]}
    monkeypatch.setattr(star_app, "body", invalid_route)
    response = asyncio.run(star_app.arbitrage_analysis(None))
    assert response.status_code == 400
    assert _json(response)["error"] == "triangle_route_must_contain_four_assets"

    async def boolean_amount(_request):
        return {"route": ["USDT", "BTC", "ETH", "USDT"], "starting_amount": True}
    monkeypatch.setattr(star_app, "body", boolean_amount)
    response = asyncio.run(star_app.arbitrage_analysis(None))
    assert response.status_code == 400
    assert _json(response)["error"] == "boolean_arbitrage_configuration_not_allowed"




def test_risk_simulation_api_is_authenticated_and_demo_only(monkeypatch):
    class FakeDB:
        def close(self): pass

    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))

    async def request_body(_request):
        return {
            "market_type": "spot",
            "initial_balance": 1000,
            "risk_per_trade": 0.01,
            "max_daily_loss": 0.07,
            "trades": [
                {"session": "day-1", "entry": 100, "stop_loss": 95, "realized_pnl": -40},
                {"session": "day-1", "entry": 100, "stop_loss": 95, "realized_pnl": -40},
                {"session": "day-1", "entry": 100, "stop_loss": 95, "realized_pnl": 10},
            ],
        }
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.risk_simulation_backtest(None))
    result = _json(response)
    assert response.status_code == 200
    assert result["accepted_trades"] == 2
    assert result["rejected_trades"] == 1
    assert result["final_equity"] == 920
    assert result["simulation_only"] is True
    assert result["no_live_orders"] is True
    assert result["environment"] == "demo"


def test_risk_simulation_api_rejects_boolean_and_oversized_inputs(monkeypatch):
    class FakeDB:
        def close(self): pass

    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))

    async def boolean_body(_request):
        return {"initial_balance": True, "trades": []}
    monkeypatch.setattr(star_app, "body", boolean_body)
    response = asyncio.run(star_app.risk_simulation_backtest(None))
    assert response.status_code == 400
    assert _json(response)["error"] == "boolean_initial_balance_not_allowed"

    async def oversized_body(_request):
        return {"trades": [{}] * 501}
    monkeypatch.setattr(star_app, "body", oversized_body)
    response = asyncio.run(star_app.risk_simulation_backtest(None))
    assert response.status_code == 400
    assert _json(response)["error"] == "trades_must_be_list_with_max_500_items"


def test_strategy_trade_outcomes_api_is_authenticated_and_returns_research_metrics(monkeypatch):
    from types import SimpleNamespace
    from app.learning import outcomes

    class FakeDB:
        def close(self): pass

    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    monkeypatch.setattr(
        outcomes, "summarize_strategy_outcomes",
        lambda db, key: {
            "ok": True, "strategy_key": key, "sample_count": 32,
            "expectancy_per_trade": 1.25, "profit_factor": 1.8,
            "sample_sufficient_for_research": True,
            "auto_tuning": False, "live_config_changed": False,
        },
    )
    request = SimpleNamespace(path_params={"strategy_key": "trend_following_ema"})
    response = asyncio.run(star_app.strategy_trade_outcomes(request))
    result = _json(response)
    assert response.status_code == 200
    assert result["strategy_key"] == "trend_following_ema"
    assert result["sample_count"] == 32
    assert result["sample_sufficient_for_research"] is True
    assert result["auto_tuning"] is False
    assert result["live_config_changed"] is False


def test_cross_margin_risk_simulation_api_uses_shared_wallet_and_verified_brackets(monkeypatch):
    class FakeDB:
        def close(self): pass

    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))

    async def request_body(_request):
        return {
            "mode": "cross_margin",
            "wallet_balance": 1000,
            "positions": [{
                "symbol": "BTCUSDT", "side": "long", "quantity": 0.01,
                "entry_price": 60000, "mark_price": 61000, "leverage": 5,
                "maintenance_brackets": [
                    {"notionalFloor": 0, "notionalCap": 50000, "maintMarginRatio": 0.004, "cum": 0},
                    {"notionalFloor": 50000, "notionalCap": 250000, "maintMarginRatio": 0.01, "cum": 300},
                ],
            }],
        }
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.risk_simulation_backtest(None))
    result = _json(response)
    assert response.status_code == 200
    assert result["margin_mode"] == "cross"
    assert result["cross_equity"] == 1010
    assert result["no_live_orders"] is True


def test_cross_margin_risk_simulation_api_rejects_missing_tiers(monkeypatch):
    class FakeDB:
        def close(self): pass

    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    async def request_body(_request):
        return {"mode": "cross_margin", "wallet_balance": 1000, "positions": [{
            "symbol": "BTCUSDT", "side": "long", "quantity": 0.01,
            "entry_price": 60000, "mark_price": 61000, "leverage": 5,
        }]}
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.risk_simulation_backtest(None))
    assert response.status_code == 400
    assert "maintenance_brackets_missing" in _json(response)["error"]


def test_learning_walk_forward_endpoint_rejects_client_authored_metrics(monkeypatch):
    class FakeDB:
        def close(self): pass

    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    response = asyncio.run(star_app.strategy_proposal_walk_forward(None))
    assert response.status_code == 410
    assert _json(response)["error"] == "historical_walk_forward_required"
