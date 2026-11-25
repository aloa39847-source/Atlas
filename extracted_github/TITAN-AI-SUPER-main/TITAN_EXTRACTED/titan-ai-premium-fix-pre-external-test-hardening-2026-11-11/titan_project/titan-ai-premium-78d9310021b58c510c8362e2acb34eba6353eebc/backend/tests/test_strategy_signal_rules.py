from types import SimpleNamespace

from app.core.enums import Timeframe
from app.engines.backtest import BacktestEngine


def _analysis(*, trend="sideways", signals=(), indicators=None, structure=None, candles=None, timeframe=Timeframe.H1):
    return SimpleNamespace(
        trend=trend, signals=list(signals), indicators=indicators or {},
        structure=structure or {}, candles=candles or {}, timeframe=timeframe,
    )


def test_trend_following_requires_multiple_directional_confirmations():
    weak = _analysis(trend="bullish", signals=["ema9_above_ema21"])
    strong = _analysis(
        trend="bullish",
        signals=["ema9_above_ema21", "supertrend_bullish"],
    )
    assert BacktestEngine._strategy_action("trend_following_ema", weak) is None
    assert BacktestEngine._strategy_action("trend_following_ema", strong) == "buy"


def test_momentum_requires_macd_and_non_extreme_oscillator_confirmation():
    overbought = _analysis(
        signals=["macd_hist_positive", "rsi_overbought"],
        indicators={"rsi14": 82, "cci20": 140},
    )
    confirmed = _analysis(
        signals=["macd_hist_positive"],
        indicators={"rsi14": 61, "stoch_rsi_k": 58},
    )
    assert BacktestEngine._strategy_action("momentum_macd_rsi", overbought) is None
    assert BacktestEngine._strategy_action("momentum_macd_rsi", confirmed) == "buy"


def test_mean_reversion_requires_band_rsi_and_secondary_oscillator():
    incomplete = _analysis(signals=["rsi_oversold", "bollinger_lower_band_touch"])
    complete = _analysis(
        signals=["rsi_oversold", "bollinger_lower_band_touch", "cci_oversold"],
    )
    assert BacktestEngine._strategy_action("mean_reversion_bbands", incomplete) is None
    assert BacktestEngine._strategy_action("mean_reversion_bbands", complete) == "buy"


def test_breakout_requires_confirmed_structure_and_quality_confirmation():
    structure = {"breakouts": [{"direction": "bullish", "status": "confirmed"}]}
    weak = _analysis(structure=structure)
    confirmed = _analysis(structure=structure, signals=["volume_spike"])
    false_breakout = _analysis(
        structure={"breakouts": [{"direction": "bullish", "status": "false_breakout"}]},
        signals=["volume_spike"],
    )
    assert BacktestEngine._strategy_action("breakout_structure", weak) is None
    assert BacktestEngine._strategy_action("breakout_structure", confirmed) == "buy"
    assert BacktestEngine._strategy_action("breakout_structure", false_breakout) is None


def test_scalping_requires_m5_momentum_volume_and_entry_filter():
    wrong_timeframe = _analysis(
        trend="bullish",
        signals=["macd_hist_positive", "volume_spike", "ema9_above_ema21"],
        timeframe=Timeframe.M15,
    )
    weak = _analysis(
        trend="bullish",
        signals=["macd_hist_positive", "volume_spike"],
        timeframe=Timeframe.M5,
    )
    confirmed = _analysis(
        trend="bullish",
        signals=["macd_hist_positive", "volume_spike", "ema9_above_ema21"],
        timeframe=Timeframe.M5,
    )
    assert BacktestEngine._strategy_action("scalping_m5", wrong_timeframe) is None
    assert BacktestEngine._strategy_action("scalping_m5", weak) is None
    assert BacktestEngine._strategy_action("scalping_m5", confirmed) == "buy"


def test_swing_requires_market_structure_macd_and_trend_filter():
    weak = _analysis(
        trend="bullish", signals=["macd_hist_positive"],
        structure={"bias": "bullish_structure"}, timeframe=Timeframe.H4,
    )
    confirmed = _analysis(
        trend="bullish", signals=["macd_hist_positive", "supertrend_bullish"],
        structure={"bias": "bullish_structure"}, timeframe=Timeframe.H4,
    )
    assert BacktestEngine._strategy_action("swing_h1_h4", weak) is None
    assert BacktestEngine._strategy_action("swing_h1_h4", confirmed) == "buy"
