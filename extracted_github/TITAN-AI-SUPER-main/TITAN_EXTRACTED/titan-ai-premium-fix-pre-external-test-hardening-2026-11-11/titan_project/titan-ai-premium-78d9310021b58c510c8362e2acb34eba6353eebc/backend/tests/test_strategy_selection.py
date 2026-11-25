from app.core.enums import MarketType
from app.engines.strategies import select_strategy


def test_strategy_selector_requires_matching_implemented_evidence():
    none = select_strategy(MarketType.SPOT, "neutral", [])
    assert none["key"] == "none"
    assert none["status"] == "no_confirmed_strategy_signal"

    bullish = select_strategy(MarketType.SPOT, "bullish", ["price_above_ema20_ema50"])
    bearish = select_strategy(MarketType.SPOT, "bearish", ["price_below_ema20_ema50"])
    assert bullish["key"] == "trend_following_ema"
    assert bearish["key"] == "trend_following_ema"

    momentum = select_strategy(MarketType.SPOT, "neutral", ["macd_hist_positive"])
    assert momentum["key"] == "momentum_macd_rsi"


def test_strategy_selector_requires_bollinger_confirmation_for_mean_reversion():
    unconfirmed = select_strategy(MarketType.SPOT, "neutral", ["rsi_oversold"])
    confirmed = select_strategy(
        MarketType.SPOT, "neutral", ["rsi_oversold", "bollinger_lower_band_touch"],
    )
    assert unconfirmed["key"] == "none"
    assert confirmed["key"] == "mean_reversion_bbands"


def test_strategy_selector_never_falls_back_to_a_planned_strategy():
    result = select_strategy(MarketType.SPOT, "neutral", ["m5_fast_move"])
    assert result["key"] == "none"
    assert result["key"] not in {"scalping_m5", "swing_h1_h4", "arbitrage"}


def test_m5_scalping_requires_m5_momentum_volume_or_candle_and_m15_alignment():
    from types import SimpleNamespace

    tf = {
        "15m": SimpleNamespace(trend="bullish", signals=[]),
        "5m": SimpleNamespace(trend="bullish", signals=["macd_hist_positive", "volume_spike"]),
    }
    result = select_strategy(MarketType.SPOT, "bullish", [], timeframe_analyses=tf)
    assert result["key"] == "scalping_m5"

    tf["5m"].signals = ["macd_hist_positive"]
    result = select_strategy(MarketType.SPOT, "bullish", [], timeframe_analyses=tf)
    assert result["key"] != "scalping_m5"


def test_swing_strategy_requires_h1_h4_and_non_conflicting_daily_alignment():
    from types import SimpleNamespace

    tf = {
        "1d": SimpleNamespace(trend="bullish", signals=[]),
        "4h": SimpleNamespace(trend="bullish", signals=["macd_hist_positive"]),
        "1h": SimpleNamespace(trend="bullish", signals=["macd_hist_positive"]),
    }
    result = select_strategy(MarketType.SPOT, "bullish", [], timeframe_analyses=tf)
    assert result["key"] == "swing_h1_h4"

    tf["1d"].trend = "bearish"
    result = select_strategy(MarketType.SPOT, "bullish", [], timeframe_analyses=tf)
    assert result["key"] != "swing_h1_h4"


def test_strategy_selector_does_not_activate_trade_strategies_for_earn():
    from types import SimpleNamespace

    tf = {
        "15m": SimpleNamespace(trend="bullish", signals=[]),
        "5m": SimpleNamespace(trend="bullish", signals=["macd_hist_positive", "volume_spike"]),
    }
    result = select_strategy(MarketType.EARN, "bullish", [], timeframe_analyses=tf)
    assert result["key"] == "none"


def test_breakout_strategy_requires_structural_direction_to_match_bias():
    bullish = select_strategy(
        MarketType.SPOT, "bullish", ["breakout_bullish", "bos_bullish"],
    )
    opposing = select_strategy(
        MarketType.SPOT, "bullish", ["breakout_bearish", "choch_bearish"],
    )
    assert bullish["key"] == "breakout_structure"
    assert opposing["key"] == "none"
    assert opposing["status"] == "structural_signal_conflicts_with_higher_timeframe_bias"


def test_strategy_selector_rejects_mixed_direction_structural_events():
    result = select_strategy(
        MarketType.SPOT, "bullish",
        ["breakout_bullish", "bos_bearish", "choch_bullish"],
    )
    assert result["key"] == "none"
    assert result["status"] == "structural_signal_conflicts_with_higher_timeframe_bias"


def test_breakout_selection_uses_only_entry_timeframe_structure():
    from types import SimpleNamespace

    higher_only = {
        "1d": SimpleNamespace(trend="bullish", signals=["breakout_bullish"]),
        "15m": SimpleNamespace(trend="bullish", signals=[]),
        "5m": SimpleNamespace(trend="bullish", signals=[]),
    }
    result = select_strategy(
        MarketType.SPOT, "bullish", ["breakout_bullish"], timeframe_analyses=higher_only,
    )
    assert result["key"] != "breakout_structure"

    higher_only["15m"].signals = ["breakout_bullish", "bos_bullish"]
    result = select_strategy(
        MarketType.SPOT, "bullish", ["breakout_bullish"], timeframe_analyses=higher_only,
    )
    assert result["key"] == "breakout_structure"
