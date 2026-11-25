import asyncio
import json
from types import SimpleNamespace

from app import star_app
from app.data.binance_public import BinancePublicData
from app.data.binance_futures_public import BinanceFuturesPublicData
from app.engines.specialized_backtest import SpecializedBacktestEngine


def _trades():
    rows = []
    for index in range(10):
        rows.append({
            "a": index + 1,
            "T": 1_000 + index * 1_000,
            "p": str(100 + index),
            "q": "2",
            "m": index >= 5,
            "s": "BTCUSDT",
        })
    return rows


def test_aggregate_trade_flow_generates_causal_signals_and_next_trade_fills():
    result = SpecializedBacktestEngine(
        initial_cash=1000, fee_rate=0, slippage_rate=0, max_notional=80,
    ).run_aggregate_trade_flow_replay(
        _trades(), rolling_window=5, entry_imbalance=0.15, exit_imbalance=0,
    )
    assert result["ok"] is True
    assert result["no_lookahead"] is True
    assert result["signal_generation"] == "causal_rolling_aggregate_taker_flow"
    assert result["trades"][0]["entry_index"] == 5
    assert result["trades"][0]["exit_index"] == 8
    assert result["trades"][0]["entry_price"] == 105
    assert result["trades"][0]["exit_price"] == 108
    assert result["data_points"] == 10
    assert result["no_live_orders"] is True


def test_aggregate_trade_flow_rejects_bad_ordering_and_mixed_symbols():
    rows = _trades()
    rows[4]["a"] = 1
    result = SpecializedBacktestEngine().run_aggregate_trade_flow_replay(rows, rolling_window=5)
    assert result["ok"] is False
    assert "invalid_aggregate_trade_4" in result["error"]

    rows = _trades()
    rows[4]["s"] = "ETHUSDT"
    result = SpecializedBacktestEngine().run_aggregate_trade_flow_replay(rows, rolling_window=5)
    assert result["ok"] is False
    assert "aggregate_trade_symbol_mismatch" in result["error"]


def test_aggregate_trade_flow_rejects_boolean_thresholds_and_insufficient_history():
    engine = SpecializedBacktestEngine()
    assert engine.run_aggregate_trade_flow_replay(
        _trades(), rolling_window=5, entry_imbalance=True,
    )["error"] == "boolean_orderflow_threshold_not_allowed"
    assert engine.run_aggregate_trade_flow_replay(
        _trades()[:5], rolling_window=5,
    )["error"] == "aggregate_trade_history_length_out_of_range"


def test_spot_historical_aggregate_trade_pagination(monkeypatch):
    adapter = BinancePublicData()
    pages = [
        [{"a": index, "T": 1000 + index, "p": "10", "q": "1", "m": False}
         for index in range(1000)],
        [{"a": index, "T": 1000 + index, "p": "10", "q": "1", "m": False}
         for index in range(1000, 1100)],
    ]
    calls = []
    def fake_get(endpoint):
        calls.append(endpoint)
        return pages.pop(0)
    monkeypatch.setattr(adapter, "_get_json", fake_get)
    result = adapter.get_aggregate_trades_history(
        "BTCUSDT", start_time=1000, end_time=5000, max_trades=2000,
    )
    assert len(result) == 1100
    assert "startTime=1000" in calls[0]
    assert "fromId=1000" in calls[1]


def test_futures_historical_aggregate_trade_pagination(monkeypatch):
    adapter = BinanceFuturesPublicData()
    pages = [
        [{"a": index, "T": 2000 + index, "p": "10", "q": "1", "m": False}
         for index in range(1000)],
        [{"a": index, "T": 2000 + index, "p": "10", "q": "1", "m": False}
         for index in range(1000, 1005)],
    ]
    calls = []
    def fake_get(endpoint):
        calls.append(endpoint)
        return pages.pop(0)
    monkeypatch.setattr(adapter, "_get_json", fake_get)
    result = adapter.get_aggregate_trades_history(
        "BTCUSDT", start_time=2000, end_time=5000, max_trades=2000,
    )
    assert len(result) == 1005
    assert "startTime=2000" in calls[0]
    assert "fromId=1000" in calls[1]


def test_binance_orderflow_api_fetches_public_historical_trades(monkeypatch):
    class FakeDB:
        def close(self): pass
        def rollback(self): pass

    rows = _trades()
    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    monkeypatch.setattr(
        "app.data.binance_public.BinancePublicData.get_aggregate_trades_history",
        lambda self, symbol, start_time, end_time, max_trades: rows,
    )

    async def request_body(_request):
        return {
            "symbol": "BTCUSDT", "market_type": "spot",
            "start_time": 1000, "end_time": 20000,
            "max_trades": 100, "rolling_window": 5,
            "initial_cash": 1000, "fee_rate": 0, "slippage_rate": 0,
        }
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.binance_orderflow_backtest(None))
    result = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert result["data_source"] == "binance_spot_historical_aggregate_trades_public_read_only"
    assert result["records_fetched"] == 10
    assert result["no_lookahead"] is True
    assert result["no_live_orders"] is True


def test_binance_orderflow_api_rejects_oversized_date_range(monkeypatch):
    class FakeDB:
        def close(self): pass
        def rollback(self): pass

    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    async def request_body(_request):
        return {"start_time": 1, "end_time": 1 + 8 * 24 * 60 * 60 * 1000}
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.binance_orderflow_backtest(None))
    result = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 400
    assert result["error"] == "start_end_time_must_be_milliseconds_within_seven_days"



def test_futures_historical_aggregate_trade_pagination_uses_usdm_endpoint(monkeypatch):
    adapter = BinanceFuturesPublicData()
    pages = [
        [{"a": index, "T": 2000 + index, "p": "10", "q": "1", "m": False}
         for index in range(1000)],
        [{"a": index, "T": 3000 + index, "p": "10", "q": "1", "m": True}
         for index in range(1000, 1005)],
    ]
    calls = []
    def fake_get(endpoint):
        calls.append(endpoint)
        return pages.pop(0)
    monkeypatch.setattr(adapter, "_get_json", fake_get)
    rows = adapter.get_aggregate_trades_history(
        "BTCUSDT", start_time=2000, end_time=5000, max_trades=2000,
    )
    assert len(rows) == 1005
    assert calls[0].startswith("/fapi/v1/aggTrades?")
    assert "startTime=2000" in calls[0]
    assert calls[1].startswith("/fapi/v1/aggTrades?")
    assert "fromId=1000" in calls[1]


def test_futures_orderflow_replay_generates_long_short_and_uses_next_trade_fill():
    trades = []
    for index in range(20):
        trades.append({
            "a": index + 1, "T": 1000 + index * 1000,
            "p": str(100 + index), "q": "1",
            "m": index >= 10, "s": "BTCUSDT",
        })
    engine = SpecializedBacktestEngine(
        initial_cash=1000, fee_rate=0, slippage_rate=0, max_notional=800,
    )
    result = engine.run_futures_aggregate_trade_flow_replay(
        trades, rolling_window=5, entry_imbalance=0.15,
        exit_imbalance=0, leverage=2, max_leverage=10, notional=800,
    )
    assert result["ok"] is True
    assert result["market_type"] == "futures"
    assert result["no_lookahead"] is True
    assert result["signal_causality"] == "verified_causal_rolling_trade_prefix"
    assert result["no_live_orders"] is True
    assert result["execution"] == "simulated_only"
    assert result["total_trades"] >= 1
    assert result["trades"][0]["entry_index"] > 0


def test_futures_orderflow_replay_rejects_invalid_trade_sequence():
    trades = [
        {"a": i + 1, "T": 1000 + i * 1000, "p": "10", "q": "1",
         "m": False, "s": "BTCUSDT"}
        for i in range(10)
    ]
    trades[5]["a"] = 1
    result = SpecializedBacktestEngine().run_futures_aggregate_trade_flow_replay(
        trades, rolling_window=5,
    )
    assert result["ok"] is False
    assert "invalid_futures_aggregate_trade_5" in result["error"]


def test_binance_orderflow_api_routes_futures_to_usdm_and_funding(monkeypatch):
    class FakeDB:
        def close(self): pass
        def rollback(self): pass

    rows = _trades()
    funding = [{"timestamp": 5000, "rate": 0.0001}]
    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    monkeypatch.setattr(
        "app.data.binance_futures_public.BinanceFuturesPublicData.get_aggregate_trades_history",
        lambda self, symbol, start_time, end_time, max_trades: rows,
    )
    monkeypatch.setattr(
        "app.data.binance_futures_public.BinanceFuturesPublicData.get_funding_rate_history",
        lambda self, symbol, start_time, end_time, limit: funding,
    )
    async def request_body(_request):
        return {
            "symbol": "BTCUSDT", "market_type": "futures",
            "start_time": 1000, "end_time": 20000,
            "max_trades": 100, "rolling_window": 5,
            "initial_cash": 1000, "fee_rate": 0, "slippage_rate": 0,
            "leverage": 2, "max_leverage": 10, "max_notional": 800,
        }
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.binance_orderflow_backtest(None))
    result = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert result["market_type"] == "futures"
    assert result["data_source"] == "binance_usdm_futures_historical_aggregate_trades_public_read_only"
    assert result["funding_records_fetched"] == 1
    assert result["no_lookahead"] is True
    assert result["no_live_orders"] is True
