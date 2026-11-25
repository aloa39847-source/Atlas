import asyncio
import json
import time
from types import SimpleNamespace

from app import star_app
from app.engines.market_data import Candles


def _rows():
    rows = []
    previous = 100.0
    start = int(time.time() * 1000) - 100 * 3_600_000
    for index in range(80):
        close = previous + 0.1
        stamp = start + index * 3_600_000
        rows.append({
            "timestamp": stamp, "close_time": stamp + 3_600_000 - 1,
            "open": previous, "high": close + 0.5,
            "low": previous - 0.5, "close": close, "volume": 1000.0,
        })
        previous = close
    rows[-1]["close_time"] = int(time.time() * 1000) - 60_000
    return rows


def _json(response):
    return json.loads(response.body.decode("utf-8"))


def test_portfolio_api_fetches_public_spot_histories_and_runs_concurrent_sleeves(monkeypatch):
    class FakeDB:
        def close(self): pass
        def rollback(self): pass

    rows = _rows()
    batch = SimpleNamespace(
        df=Candles(rows),
        source=SimpleNamespace(value="live_public"),
        market_type="spot",
    )
    fetched = []
    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    monkeypatch.setattr(
        star_app.core_service.market_data, "get_public_candles",
        lambda symbol, timeframe, limit, market_type: (fetched.append(symbol) or batch),
    )

    async def request_body(_request):
        return {
            "symbols": ["BTCUSDT", "ETHUSDT"], "market_type": "spot",
            "strategy_key": "trend_following_ema", "timeframe": "1h",
            "initial_cash": 1000, "max_positions": 2, "limit": 80,
        }
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.portfolio_backtest(None))
    result = _json(response)
    assert response.status_code == 200
    assert fetched == ["BTCUSDT", "ETHUSDT"]
    assert result["symbols_succeeded"] == 2
    assert result["capital_per_symbol"] == 500
    assert result["position_model"] == "one_net_position_per_symbol_strategy_sleeve_with_optional_bounded_pyramiding"
    assert result["execution"] == "simulated_only"
    assert result["no_live_orders"] is True


def test_portfolio_api_rejects_non_usdt_and_duplicate_symbols(monkeypatch):
    class FakeDB:
        def close(self): pass
        def rollback(self): pass

    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    async def request_body(_request):
        return {"symbols": ["BTCUSDT", "BTC/USDT"]}
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.portfolio_backtest(None))
    assert response.status_code == 400
    assert _json(response)["error"] == "duplicate_portfolio_symbol"


def test_portfolio_api_accepts_multiple_strategies_and_allocates_four_sleeves(monkeypatch):
    from app.engines.strategy_backtest_suite import StrategyBacktestSuite

    class FakeDB:
        def close(self): pass
        def rollback(self): pass

    rows = _rows()
    batch = SimpleNamespace(
        df=Candles(rows),
        source=SimpleNamespace(value="live_public"),
        market_type="spot",
    )
    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    monkeypatch.setattr(
        star_app.core_service.market_data, "get_public_candles",
        lambda symbol, timeframe, limit, market_type: batch,
    )

    def fake_compare(self, candles, *, market_type, timeframe, strategy_keys,
                     initial_cash, fee_rate, slippage_rate, max_notional,
                     max_leverage, leverage, funding_events, **kwargs):
        return {
            "ok": True,
            "leaderboard": [{
                "ok": True, "strategy_key": strategy_keys[0],
                "final_equity": initial_cash, "total_return": 0,
                "total_trades": 0, "fees_paid": 0, "slippage_cost": 0,
                "funding_net": 0, "liquidations": 0,
                "equity_curve": [initial_cash] * len(candles), "trades": [],
            }],
        }
    monkeypatch.setattr(StrategyBacktestSuite, "compare", fake_compare)

    async def request_body(_request):
        return {
            "symbols": ["BTCUSDT", "ETHUSDT"], "market_type": "spot",
            "strategy_keys": ["trend_following_ema", "momentum_macd_rsi"],
            "timeframe": "1h", "initial_cash": 1000, "max_positions": 4, "limit": 80,
        }
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.portfolio_backtest(None))
    result = _json(response)
    assert response.status_code == 200
    assert result["strategy_keys"] == ["trend_following_ema", "momentum_macd_rsi"]
    assert result["sleeves_requested"] == 4
    assert result["capital_per_sleeve"] == 250
    assert result["max_positions"] == 4


def test_dynamic_portfolio_uses_only_confirmed_demo_trade_ledger_rows(monkeypatch):
    from app.engines.portfolio_backtest import PortfolioBacktestEngine
    from app.models.entities import LearningRecord

    class FakeDB:
        def __init__(self, rows): self.rows = rows
        def close(self): pass
        def rollback(self): pass
        def execute(self, _query):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self.rows))

    rows = _rows()
    batch = SimpleNamespace(
        df=Candles(rows), source=SimpleNamespace(value="live_public"), market_type="spot",
    )
    ledger = [
        LearningRecord(environment_mode="demo", category="trade_outcome", subject="1",
            evidence={"strategy_key": "trend_following_ema", "market_regime": "trend",
                      "market_type": "spot", "net_pnl": 10.0, "entry_price": 100.0,
                      "quantity_closed": 1.0, "source": "binance_demo_confirmed_fills",
                      "research_only": True}),
        LearningRecord(environment_mode="demo", category="trade_outcome", subject="2",
            evidence={"strategy_key": "trend_following_ema", "market_regime": "trend",
                      "market_type": "spot", "net_pnl": 999.0, "entry_price": 100.0,
                      "quantity_closed": 1.0}),
        LearningRecord(environment_mode="demo", category="trade_outcome", subject="3",
            evidence={"strategy_key": "momentum_macd_rsi", "market_regime": "trend",
                      "market_type": "spot", "net_pnl": 5.0, "entry_price": 100.0,
                      "quantity_closed": 1.0, "source": "binance_demo_confirmed_fills",
                      "research_only": True}),
    ]
    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB(ledger))
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    monkeypatch.setattr(
        star_app.core_service.market_data, "get_public_candles",
        lambda symbol, timeframe, limit, market_type: batch,
    )
    captured = {}
    def fake_run(self, histories, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "symbols_tested": len(histories)}
    monkeypatch.setattr(PortfolioBacktestEngine, "run", fake_run)

    async def request_body(_request):
        return {
            "symbols": ["BTCUSDT", "ETHUSDT"], "market_type": "spot",
            "strategy_keys": ["trend_following_ema", "momentum_macd_rsi"],
            "timeframe": "1h", "initial_cash": 1000, "max_positions": 4, "limit": 80,
            "dynamic_allocation_enabled": True, "allocation_regime": "trend",
        }
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.portfolio_backtest(None))
    assert response.status_code == 200
    outcomes = captured["strategy_outcomes"]
    assert outcomes["trend_following_ema"] == [
        {"closed": True, "regime": "trend", "net_return": 0.1}
    ]
    assert outcomes["momentum_macd_rsi"] == [
        {"closed": True, "regime": "trend", "net_return": 0.05}
    ]
