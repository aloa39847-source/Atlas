from datetime import datetime, timezone

from app.core.enums import Timeframe
from app.engines.market_data import MarketDataEngine
from app.engines.technical import TechnicalAnalysisEngine, _vwap


def test_all_fifteen_timeframes_generate_synthetic_candles_and_ema200_history():
    timeframes = list(Timeframe)
    assert len(timeframes) == 15
    batches = MarketDataEngine().get_multi_timeframe(
        "BTC/USDT", limit=250, seed=7, timeframes=timeframes,
    )
    assert len(batches) == 15
    engine = TechnicalAnalysisEngine()
    for timeframe, batch in batches.items():
        assert len(batch.df) == 250
        analysis = engine.analyze(batch.df, timeframe)
        assert analysis.indicators["ema200"] is not None, timeframe.value


def test_multi_timeframe_fetch_defaults_to_all_fifteen_frames(monkeypatch):
    engine = MarketDataEngine()
    calls = []

    def fake_get_public_candles(symbol, timeframe, *, limit, market_type):
        calls.append((timeframe, limit, market_type))
        return (symbol, timeframe, limit, market_type)

    monkeypatch.setattr(engine, "get_public_candles", fake_get_public_candles)
    core = engine.get_public_multi_timeframe("BTCUSDT")
    assert len(core) == 15
    assert [call[0] for call in calls[:5]] == [
        Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5,
    ]
    assert all(row[2] == 250 for row in core.values())

    calls.clear()
    full = engine.get_public_multi_timeframe(
        "BTCUSDT", timeframes=list(Timeframe), market_type="futures",
    )
    assert len(full) == 15
    assert len(calls) == 15
    assert all(row[2] == 250 and row[3] == "futures" for row in full.values())


def test_vwap_resets_at_utc_session_boundary_when_timestamps_are_available():
    values = _vwap(
        [10.0, 20.0], [10.0, 20.0], [10.0, 20.0], [1.0, 1.0],
        ["2026-11-09T23:59:00Z", "2026-11-10T00:00:00Z"],
    )
    assert values == [10.0, 20.0]


def test_vwap_uses_one_batch_anchor_without_timestamps():
    values = _vwap([10.0, 20.0], [10.0, 20.0], [10.0, 20.0], [1.0, 1.0])
    assert values == [10.0, 15.0]


def test_vwap_accepts_datetime_and_millisecond_timestamps():
    values = _vwap(
        [10.0, 20.0], [10.0, 20.0], [10.0, 20.0], [1.0, 1.0],
        [
            datetime(2026, 11, 9, 23, 59, tzinfo=timezone.utc),
            1794268800000,
        ],
    )
    assert values == [10.0, 20.0]


def test_public_candle_fetch_uses_only_closed_spot_and_futures_candles(monkeypatch):
    import pytest
    import app.engines.market_data as market_data_module

    now_ms = 1_000_000
    monkeypatch.setattr(market_data_module.time, "time", lambda: now_ms / 1000)

    class FakeAdapter:
        def get_klines(self, symbol, interval, limit):
            return []

        def klines_to_candles(self, _):
            return rows

    monkeypatch.setattr(market_data_module, "BinancePublicData", FakeAdapter)
    monkeypatch.setattr(market_data_module, "BinanceFuturesPublicData", FakeAdapter)
    engine = MarketDataEngine()

    rows = [
        {"timestamp": i * 60_000, "close_time": now_ms - 10_000 + i}
        for i in range(30)
    ] + [{"timestamp": now_ms, "close_time": now_ms + 60_000}]

    for market_type in ("spot", "futures"):
        batch = engine.get_public_candles("BTCUSDT", Timeframe.M1, market_type=market_type)
        assert len(batch.df) == 30
        assert all(row["close_time"] <= now_ms for row in batch.df.rows)
        assert batch.market_type == market_type

    rows = [
        {"timestamp": i * 60_000, "close_time": now_ms - 10_000 + i}
        for i in range(29)
    ] + [{"timestamp": now_ms, "close_time": now_ms + 60_000}]

    for market_type in ("spot", "futures"):
        with pytest.raises(RuntimeError, match="insufficient_binance_public_candles"):
            engine.get_public_candles("BTCUSDT", Timeframe.M1, market_type=market_type)


def test_default_public_fetch_skips_failed_optional_frame_but_requires_core(monkeypatch):
    import pytest

    engine = MarketDataEngine()
    calls = []

    def fake_get_public_candles(symbol, timeframe, *, limit, market_type):
        calls.append(timeframe)
        if timeframe == Timeframe.M1:
            raise RuntimeError("optional_interval_unavailable")
        return (symbol, timeframe, limit, market_type)

    monkeypatch.setattr(engine, "get_public_candles", fake_get_public_candles)
    batches = engine.get_public_multi_timeframe("BTCUSDT")

    assert Timeframe.M1 not in batches
    assert all(frame in batches for frame in (
        Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5,
    ))
    assert len(batches) == 14

    def missing_core(symbol, timeframe, *, limit, market_type):
        if timeframe == Timeframe.M5:
            raise RuntimeError("core_interval_unavailable")
        return (symbol, timeframe, limit, market_type)

    monkeypatch.setattr(engine, "get_public_candles", missing_core)
    with pytest.raises(RuntimeError, match="core_interval_unavailable"):
        engine.get_public_multi_timeframe("BTCUSDT")


def test_explicit_public_timeframe_selection_remains_strict(monkeypatch):
    import pytest

    engine = MarketDataEngine()

    def unavailable(symbol, timeframe, *, limit, market_type):
        raise RuntimeError("explicit_interval_unavailable")

    monkeypatch.setattr(engine, "get_public_candles", unavailable)
    with pytest.raises(RuntimeError, match="explicit_interval_unavailable"):
        engine.get_public_multi_timeframe(
            "BTCUSDT", timeframes=[Timeframe.M1],
        )
