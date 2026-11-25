import asyncio
import json
import time
from types import SimpleNamespace

from app import star_app
from app.engines.market_data import Candles
from app.engines.specialized_backtest import SpecializedBacktestEngine


ROUTE = ["USDT", "BTC", "ETH", "USDT"]
PAIRS = ["BTCUSDT", "ETHBTC", "ETHUSDT"]


def _candle_rows(open_values, close_values):
    rows = []
    for index, (open_price, close_price) in enumerate(zip(open_values, close_values)):
        rows.append({
            "timestamp": (index + 1) * 3_600_000,
            "open": open_price,
            "high": max(open_price, close_price) * 1.01,
            "low": min(open_price, close_price) * 0.99,
            "close": close_price,
            "volume": 1000,
        })
    return rows


def _histories():
    return {
        "BTCUSDT": _candle_rows([100, 100, 100], [100, 100, 100]),
        "ETHBTC": _candle_rows([0.05, 0.05, 0.05], [0.05, 0.05, 0.05]),
        "ETHUSDT": _candle_rows([6, 6, 4.5], [6, 4.5, 4.5]),
    }


def test_triangle_candle_replay_uses_close_signal_and_next_open_execution():
    result = SpecializedBacktestEngine(
        initial_cash=100, fee_rate=0, slippage_rate=0,
    ).run_triangular_candle_replay(
        _histories(), route=ROUTE, pair_symbols=PAIRS, min_profit_pct=5,
    )
    assert result["ok"] is True
    assert result["total_trades"] == 1
    cycle = result["cycles"][0]
    assert cycle["signal_index"] == 0
    assert cycle["fill_index"] == 1
    assert cycle["signal_estimated_profit_percent"] == 20
    assert cycle["ending_amount"] == 120
    assert result["final_equity"] == 120
    assert result["no_lookahead"] is True
    assert result["no_live_orders"] is True


def test_triangle_candle_replay_accounts_for_three_leg_fees_and_slippage():
    result = SpecializedBacktestEngine(
        initial_cash=100, fee_rate=0.001, slippage_rate=0.0005,
    ).run_triangular_candle_replay(
        _histories(), route=ROUTE, pair_symbols=PAIRS, min_profit_pct=5,
    )
    assert result["ok"] is True
    cycle = result["cycles"][0]
    assert cycle["estimated_fees_in_start_asset"] > 0
    assert cycle["estimated_slippage_in_start_asset"] > 0
    assert cycle["ending_amount"] < 120


def test_triangle_candle_replay_rejects_bad_pair_orientation_and_unaligned_history():
    histories = _histories()
    histories["BTCBUSD"] = histories.pop("ETHBTC")
    result = SpecializedBacktestEngine().run_triangular_candle_replay(
        histories, route=ROUTE, pair_symbols=["BTCUSDT", "BTCBUSD", "ETHUSDT"],
    )
    assert result["ok"] is False
    assert result["error"] == "pair_symbol_does_not_match_route_leg_1"

    histories = _histories()
    histories["ETHUSDT"][-1]["timestamp"] += 1
    result = SpecializedBacktestEngine().run_triangular_candle_replay(
        histories, route=ROUTE, pair_symbols=PAIRS,
    )
    assert result["ok"] is True
    assert result["data_points"] == 2


def test_binance_triangle_api_fetches_three_public_pairs(monkeypatch):
    class FakeDB:
        def close(self): pass
        def rollback(self): pass

    rows = []
    start = int(time.time() * 1000) - 80 * 3_600_000
    for index in range(60):
        stamp = start + index * 3_600_000
        rows.append({
            "timestamp": stamp, "close_time": stamp + 3_600_000 - 1,
            "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000,
        })
    rows[-1]["close_time"] = int(time.time() * 1000) - 60_000
    batch = SimpleNamespace(df=Candles(rows), source=SimpleNamespace(value="live_public"))
    fetched = []
    def fake_candles(symbol, timeframe, limit, market_type):
        fetched.append(symbol)
        if symbol not in PAIRS:
            raise RuntimeError("symbol_not_found")
        return batch

    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    monkeypatch.setattr(star_app.core_service.market_data, "get_public_candles", fake_candles)

    async def request_body(_request):
        return {
            "route": ROUTE, "timeframe": "1h", "limit": 60,
            "initial_cash": 100, "fee_rate": 0, "slippage_rate": 0,
            "min_profit_pct": 5,
        }
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.binance_triangular_arbitrage_backtest(None))
    result = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert result["pair_symbols"] == PAIRS
    assert result["pair_histories_fetched"] == 3
    assert result["data_source"] == "binance_spot_public_ohlcv_read_only"
    assert result["execution"] == "simulated_only"
    assert result["no_live_orders"] is True
    assert fetched == ["USDTBTC", "BTCUSDT", "BTCETH", "ETHBTC", "ETHUSDT"]


def test_binance_triangle_api_rejects_open_or_duplicate_route(monkeypatch):
    class FakeDB:
        def close(self): pass
        def rollback(self): pass

    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, None))
    async def request_body(_request):
        return {"route": ["USDT", "BTC", "ETH", "BTC"]}
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.binance_triangular_arbitrage_backtest(None))
    result = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 400
    assert result["error"] == "route_must_be_three_distinct_closed_assets"
