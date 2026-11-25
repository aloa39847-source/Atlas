import pytest

from app.core.enums import Timeframe
from app.data.binance_futures_public import BinanceFuturesPublicData
from app.engines.market_data import MarketDataEngine


def _kline(timestamp):
    return [
        timestamp, "100", "102", "99", "101", "2",
        timestamp + 59_999,
    ]


def test_futures_public_adapter_validates_symbol_interval_and_limit():
    adapter = BinanceFuturesPublicData()
    with pytest.raises(ValueError, match="invalid_futures_symbol"):
        adapter.get_klines("BTCUSDT&limit=1000", "5m", 120)
    with pytest.raises(ValueError, match="invalid_futures_klines"):
        adapter.get_klines("BTCUSDT", "2m", 120)
    with pytest.raises(ValueError, match="invalid_futures_klines"):
        adapter.get_klines("BTCUSDT", "5m", True)


def test_futures_public_adapter_parses_klines_and_mark_price(monkeypatch):
    adapter = BinanceFuturesPublicData()
    now_ms = 1_700_000_000_000
    monkeypatch.setattr("app.data.binance_futures_public.time.time", lambda: now_ms / 1000.0)
    monkeypatch.setattr(adapter, "_get_json", lambda endpoint: (
        [_kline(now_ms + i * 60_000) for i in range(30)]
        if "/klines?" in endpoint
        else {
            "markPrice": "101.2", "indexPrice": "101.0",
            "lastFundingRate": "0.0001", "nextFundingTime": now_ms + 8 * 60 * 60 * 1000,
                "time": now_ms,
        }
    ))
    rows = adapter.klines_to_candles(adapter.get_klines("BTC/USDT", "5m", 30))
    assert len(rows) == 30
    mark = adapter.get_mark_price("BTC/USDT")
    assert mark["mark_price"] == 101.2
    assert mark["index_price"] == 101.0
    assert mark["source"] == "binance_usdm_futures_public_read_only"


def test_market_data_engine_keeps_futures_source_and_market_label(monkeypatch):
    now_ms = 1_700_000_000_000
    monkeypatch.setattr(
        BinanceFuturesPublicData, "get_klines",
        lambda self, symbol, interval, limit: [_kline(now_ms + i * 60_000) for i in range(120)],
    )
    batch = MarketDataEngine().get_public_candles("BTC/USDT", Timeframe.H1, market_type="futures")
    assert batch.market_type == "futures"
    assert batch.source.value == "live_public"
    assert "FUTURES" in batch.note


def test_market_data_engine_uses_futures_depth_and_aggregate_trade_sources(monkeypatch):
    now_ms = 1_700_000_002_000
    monkeypatch.setattr("app.engines.market_data.time.time", lambda: now_ms / 1000.0)
    monkeypatch.setattr(BinanceFuturesPublicData, "get_order_book", lambda self, symbol, limit: {
        "lastUpdateId": 7, "bids": [["99", "2"], ["98", "3"]],
        "asks": [["101", "1"], ["102", "4"]],
    })
    monkeypatch.setattr(BinanceFuturesPublicData, "get_aggregate_trades", lambda self, symbol, limit: [
        {"p": "100", "q": "2", "T": 1_700_000_000_000, "m": False},
        {"p": "101", "q": "1", "T": 1_700_000_001_000, "m": True},
    ])
    engine = MarketDataEngine()
    depth = engine.get_public_order_book("BTC/USDT", market_type="futures")
    flow = engine.get_public_order_flow("BTC/USDT", limit=2, market_type="futures")
    assert depth["source"] == "binance_futures_public_read_only"
    assert flow["source"] == "binance_futures_public_read_only"
    assert flow["trade_count"] == 2


def test_futures_portfolio_prices_use_fresh_mark_price_not_last_trade(monkeypatch):
    now_ms = 1_800_000_000_000
    monkeypatch.setattr("app.data.binance_futures_public.time.time", lambda: now_ms / 1000.0)
    adapter = BinanceFuturesPublicData()
    monkeypatch.setattr(adapter, "_get_json", lambda endpoint: [
        {
            "symbol": "BTCUSDT", "markPrice": "101.5", "indexPrice": "101.4",
            "lastFundingRate": "0.0001", "time": now_ms,
        },
        {
            "symbol": "ETHUSDT", "markPrice": "2000", "indexPrice": "2000",
            "lastFundingRate": "0.0001", "time": now_ms - 600_000,
        },
    ])
    prices = adapter.get_mark_prices({"BTC/USDT", "ETH/USDT"})
    assert prices == {"BTC/USDT": 101.5}


def test_futures_mark_price_rejects_stale_single_symbol_response(monkeypatch):
    now_ms = 1_800_000_000_000
    monkeypatch.setattr("app.data.binance_futures_public.time.time", lambda: now_ms / 1000.0)
    adapter = BinanceFuturesPublicData()
    monkeypatch.setattr(adapter, "_get_json", lambda endpoint: {
        "symbol": "BTCUSDT", "markPrice": "101.5", "indexPrice": "101.4",
        "lastFundingRate": "0.0001", "time": now_ms - 600_000,
    })
    with pytest.raises(RuntimeError, match="stale_mark_price"):
        adapter.get_mark_price("BTC/USDT")


def test_futures_mark_price_rejects_boolean_numeric_fields(monkeypatch):
    adapter = BinanceFuturesPublicData()
    monkeypatch.setattr(adapter, "_get_json", lambda endpoint: {
        "symbol": "BTCUSDT", "markPrice": True, "indexPrice": "100",
        "lastFundingRate": "0.0001", "time": 1_800_000_000_000,
    })
    with pytest.raises(RuntimeError, match="invalid_mark_price"):
        adapter.get_mark_price("BTC/USDT")


def test_futures_funding_history_validates_and_parses_public_rows(monkeypatch):
    adapter = BinanceFuturesPublicData()
    monkeypatch.setattr(adapter, "_get_json", lambda endpoint: [
        {"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "0.0001"},
        {"symbol": "BTCUSDT", "fundingTime": 2000, "fundingRate": "-0.0002"},
    ])
    result = adapter.get_funding_rate_history("BTC/USDT", start_time=1000, end_time=3000)
    assert result[0]["timestamp"] == 1000
    assert result[1]["rate"] == -0.0002
    assert result[0]["source"] == "binance_usdm_futures_public_read_only"


def test_futures_funding_history_rejects_boolean_or_out_of_range_rows(monkeypatch):
    adapter = BinanceFuturesPublicData()
    monkeypatch.setattr(adapter, "_get_json", lambda endpoint: [
        {"symbol": "BTCUSDT", "fundingTime": True, "fundingRate": "0.0001"},
    ])
    with pytest.raises(RuntimeError, match="invalid_funding_row"):
        adapter.get_funding_rate_history("BTC/USDT", start_time=1000, end_time=3000)
    with pytest.raises(ValueError, match="invalid_futures_funding_history_range_or_limit"):
        adapter.get_funding_rate_history("BTC/USDT", start_time=True, end_time=3000)


def test_market_data_engine_paginates_funding_history_without_gaps(monkeypatch):
    calls = []
    def page(self, symbol, *, start_time, end_time, limit):
        calls.append(start_time)
        if len(calls) == 1:
            return [{"timestamp": 1000 + index, "rate": 0.001} for index in range(1000)]
        return [{"timestamp": 3000, "rate": 0.003}]
    monkeypatch.setattr(BinanceFuturesPublicData, "get_funding_rate_history", page)
    result = MarketDataEngine().get_public_funding_rates("BTC/USDT", start_time=1000, end_time=3000)
    assert len(result) == 1001
    assert result[0]["timestamp"] == 1000
    assert result[-1]["timestamp"] == 3000
    assert calls == [1000, 2000]
