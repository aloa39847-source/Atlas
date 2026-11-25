import pytest

from app.data.binance_public import BinancePublicData


def _row(timestamp, open_price="100", high="102", low="99", close="101", volume="2"):
    return [
        timestamp, open_price, high, low, close, volume,
        timestamp + 299_999,
    ]


def test_klines_to_candles_validates_and_normalizes_ohlcv():
    candles = BinancePublicData().klines_to_candles([_row(1_700_000_000_000)])
    assert len(candles) == 1
    assert candles[0]["open"] == 100.0
    assert candles[0]["high"] == 102.0
    assert candles[0]["low"] == 99.0
    assert candles[0]["close"] == 101.0
    assert candles[0]["volume"] == 2.0


@pytest.mark.parametrize("row", [
    _row(1_700_000_000_000, high="98"),
    _row(1_700_000_000_000, low="103"),
    _row(1_700_000_000_000, volume="-1"),
    _row(1_700_000_000_000, close="nan"),
    _row(1_700_000_000_000)[:6],
])
def test_klines_to_candles_rejects_invalid_rows(row):
    with pytest.raises(RuntimeError):
        BinancePublicData().klines_to_candles([row])


def test_klines_to_candles_rejects_non_increasing_timestamps():
    first = _row(1_700_000_000_000)
    second = _row(1_700_000_000_000)
    with pytest.raises(RuntimeError, match="chronology|integrity"):
        BinancePublicData().klines_to_candles([first, second])


@pytest.mark.parametrize("symbol,interval,limit", [
    ("BTC/USDT&limit=1000", "5m", 120),
    ("BTC/USDT", "2y", 120),
    ("BTC/USDT", "5m", True),
    ("BTC/USDT", "5m", 10),
])
def test_get_klines_rejects_unapproved_query_parameters(symbol, interval, limit):
    with pytest.raises(ValueError, match="invalid_klines"):
        BinancePublicData().get_klines(symbol, interval, limit)


@pytest.mark.parametrize("interval", [
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h",
    "6h", "8h", "12h", "1d", "3d", "1w", "1M",
])
def test_get_klines_accepts_all_fifteen_project_timeframes(interval, monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b"[]"

    monkeypatch.setattr("app.data.binance_public.urllib.request.urlopen", lambda *args, **kwargs: FakeResponse())
    assert BinancePublicData().get_klines("BTC/USDT", interval, 120) == []
