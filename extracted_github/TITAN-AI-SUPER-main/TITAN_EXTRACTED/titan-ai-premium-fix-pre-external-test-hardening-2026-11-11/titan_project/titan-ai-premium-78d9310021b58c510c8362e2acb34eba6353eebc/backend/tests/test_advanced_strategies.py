from types import SimpleNamespace
from datetime import datetime, timezone

from app.core.enums import MarketType
from app.engines.strategies import STRATEGIES, evaluate_strategy_candidates


def _tf(trend="bullish", signals=(), indicators=None, structure=None):
    return SimpleNamespace(trend=trend, signals=list(signals), indicators=indicators or {}, structure=structure or {})


def test_all_requested_advanced_strategies_are_registered():
    keys = {item["key"] for item in STRATEGIES}
    expected = {
        "adx_trend", "ichimoku_cloud", "parabolic_sar", "multi_timeframe_trend",
        "order_block", "fair_value_gap", "liquidity_sweep", "breaker_block",
        "premium_discount", "volume_profile", "vwap_reversion", "funding_rate",
        "open_interest_divergence", "statistical_arbitrage", "pairs_trading",
        "opening_range_breakout", "vwap_scalping", "order_book_imbalance",
        "cumulative_volume_delta", "momentum_burst",
    }
    assert expected <= keys


def test_advanced_strategies_are_scored_and_do_not_auto_qualify_without_evidence():
    result = evaluate_strategy_candidates(MarketType.SPOT, "neutral", timeframe_analyses={})
    by_key = {item["key"]: item for item in result["candidates"]}
    assert all(key in by_key for key in {
        "adx_trend", "ichimoku_cloud", "parabolic_sar", "multi_timeframe_trend",
        "order_block", "fair_value_gap", "liquidity_sweep", "breaker_block",
        "premium_discount", "volume_profile", "vwap_reversion", "vwap_scalping",
        "momentum_burst",
    })
    assert all(not by_key[key]["qualified"] for key in by_key if key not in {"trend_following_ema", "momentum_macd_rsi", "mean_reversion_bbands", "breakout_structure", "scalping_m5", "swing_h1_h4", "liquidity_orderflow", "arbitrage"})


def test_mtf_and_ichimoku_need_confirmed_directional_evidence():
    tf = {
        "1d": _tf("bullish"),
        "4h": _tf("bullish", ["price_above_ichimoku_cloud"], {"ichimoku_tenkan": 110, "ichimoku_kijun": 100}),
        "1h": _tf("bullish"),
        "15m": _tf("bullish"),
        "5m": _tf("bullish"),
    }
    result = evaluate_strategy_candidates(MarketType.SPOT, "bullish", timeframe_analyses=tf)
    by_key = {item["key"]: item for item in result["candidates"]}
    assert by_key["multi_timeframe_trend"]["qualified"]
    assert by_key["ichimoku_cloud"]["qualified"]


def test_futures_external_context_rejects_stale_or_untrusted_funding_data():
    tf = {name: _tf("bullish") for name in ("1d", "4h", "1h", "15m", "5m")}
    old = {
        "ok": True, "source": "binance_futures_public_read_only",
        "symbol": "BTCUSDT", "fetched_at": "2020-01-01T00:00:00+00:00",
        "funding_rate": 0.001, "direction": "bullish",
    }
    result = evaluate_strategy_candidates(
        MarketType.FUTURES, "bullish", timeframe_analyses=tf,
        strategy_context={"funding_rate": old}, expected_symbol="BTCUSDT",
    )
    funding = next(x for x in result["candidates"] if x["key"] == "funding_rate")
    assert not funding["qualified"]
    assert funding["score"] == 0


def test_spot_does_not_activate_futures_only_funding_strategy():
    result = evaluate_strategy_candidates(MarketType.SPOT, "neutral", timeframe_analyses={})
    assert not any(x["key"] == "funding_rate" for x in result["candidates"])
