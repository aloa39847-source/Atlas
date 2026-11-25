from app.core.enums import Timeframe
from app.engines.market_data import Candles
from app.engines.technical import TechnicalAnalysisEngine


def test_confirmed_close_breakout_is_exposed_as_strategy_signal():
    rows = []
    for index in range(40):
        rows.append({
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 100.0,
        })
    rows[-1] = {
        "open": 103.0,
        "high": 105.0,
        "low": 102.5,
        "close": 104.0,
        "volume": 250.0,
    }
    result = TechnicalAnalysisEngine().analyze(Candles(rows), Timeframe.M15)
    assert "breakout_bullish" in result.signals
    assert any(event["direction"] == "bullish" for event in result.structure["bos_choch"])


def test_ema50_is_not_substituted_with_a_shorter_ema():
    rows = []
    for index in range(40):
        price = 100.0 + index
        rows.append({
            "open": price - 0.2, "high": price + 0.5,
            "low": price - 0.5, "close": price, "volume": 100.0,
        })
    short = TechnicalAnalysisEngine().analyze(Candles(rows), Timeframe.H1)
    assert short.indicators["ema50"] is None
    assert "price_above_ema20_ema50" not in short.signals

    for index in range(20):
        price = 140.0 + index
        rows.append({
            "open": price - 0.2, "high": price + 0.5,
            "low": price - 0.5, "close": price, "volume": 100.0,
        })
    full = TechnicalAnalysisEngine().analyze(Candles(rows), Timeframe.H1)
    assert full.indicators["ema50"] is not None
    assert "price_above_ema20_ema50" in full.signals


def test_technical_analysis_rejects_non_finite_or_inconsistent_ohlcv():
    rows = []
    for index in range(40):
        price = 100.0 + index
        rows.append({
            "open": price - 0.2, "high": price + 0.5,
            "low": price - 0.5, "close": price, "volume": 100.0,
        })
    rows[-1] = {**rows[-1], "high": 1.0}
    invalid_ohlc = TechnicalAnalysisEngine().analyze(Candles(rows), Timeframe.M5)
    assert "invalid_ohlcv_integrity" in invalid_ohlc.signals

    rows[-1] = {**rows[-1], "high": 140.5, "volume": float("nan")}
    invalid_volume = TechnicalAnalysisEngine().analyze(Candles(rows), Timeframe.M5)
    assert "invalid_ohlcv_integrity" in invalid_volume.signals


def test_vwap_resets_at_utc_session_boundary_when_timestamps_exist():
    rows = [
        {"open": 10, "high": 11, "low": 9, "close": 10, "volume": 0,
         "timestamp": 1_794_240_000_000}
        for _ in range(30)
    ]
    rows[-2] = {
        "open": 10, "high": 11, "low": 9, "close": 10, "volume": 2,
        "timestamp": 1_794_240_060_000,
    }
    rows[-1] = {
        "open": 30, "high": 31, "low": 29, "close": 30, "volume": 2,
        "timestamp": 1_794_326_400_000,
    }
    result = TechnicalAnalysisEngine().analyze(Candles(rows), Timeframe.M5)
    assert result.indicators["vwap"] == 30


def test_vwap_uses_volume_weighting_within_one_session():
    rows = [
        {"open": 10, "high": 11, "low": 9, "close": 10, "volume": 0,
         "timestamp": 1_794_240_000_000}
        for _ in range(30)
    ]
    rows[-2] = {
        "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1,
        "timestamp": 1_794_240_060_000,
    }
    rows[-1] = {
        "open": 20, "high": 21, "low": 19, "close": 20, "volume": 3,
        "timestamp": 1_794_240_120_000,
    }
    result = TechnicalAnalysisEngine().analyze(Candles(rows), Timeframe.M5)
    assert result.indicators["vwap"] == 17.5

def test_smc_reports_liquidity_sweep_fvg_and_causal_metadata():
    rows = []
    for i in range(45):
        price = 100 + (i % 8) * 0.2
        rows.append({"open":price,"high":price+1,"low":price-1,"close":price+0.2,"volume":100})
    rows[-3] = {"open":100,"high":105,"low":99,"close":104,"volume":100}
    rows[-2] = {"open":104,"high":106,"low":103,"close":105,"volume":120}
    rows[-1] = {"open":105,"high":107,"low":104,"close":106,"volume":300}
    result = TechnicalAnalysisEngine().analyze(Candles(rows), Timeframe.M15)
    assert result.structure["structure_causality"] == "confirmed_pivots_and_current_completed_candle_only"
    assert "liquidity_sweeps" in result.structure
    assert "fair_value_gaps" in result.structure
    assert "order_blocks" in result.structure
    assert result.structure["pivot_confirmation_bars"] == 3


def test_smc_confirms_pivot_only_after_right_side_bars_and_detects_liquidity_sweep():
    highs=[101.0]*40
    lows=[99.0]*40
    closes=[100.0]*40
    opens=[100.0]*40
    highs[34]=110.0
    highs[39]=111.0
    lows[39]=108.0
    opens[39]=102.0
    closes[39]=109.0
    # The gap is measured against the high two bars before the current candle.
    highs[37]=102.0
    structure=TechnicalAnalysisEngine()._market_structure(
        highs,lows,closes,open_prices=opens,volumes=[100.0]*40,
    )
    sweep=next(item for item in structure["liquidity_sweeps"] if item["type"]=="buy_side_liquidity_sweep")
    assert sweep["pivot_index"]==34
    assert sweep["pivot_confirmed_at"]==37
    assert sweep["confirmation_index"]==39
    assert any(item["direction"]=="bullish" for item in structure["fair_value_gaps"])


def test_smc_regime_context_distinguishes_trend_from_range():
    engine=TechnicalAnalysisEngine()
    range_closes=[100.0]*30
    ranging=engine._market_structure(
        [101.0]*30,[99.0]*30,range_closes,
        open_prices=[100.0]*30,volumes=[100.0]*30,
    )
    trend_closes=[100.0+i for i in range(30)]
    trending=engine._market_structure(
        [x+1 for x in trend_closes],[x-1 for x in trend_closes],trend_closes,
        open_prices=[x-0.2 for x in trend_closes],volumes=[100.0]*30,
    )
    assert ranging["market_regime"]["trend"]=="ranging"
    assert trending["market_regime"]["trend"]=="trending_bullish"
    assert trending["market_regime"]["directional_efficiency"]>0.5
