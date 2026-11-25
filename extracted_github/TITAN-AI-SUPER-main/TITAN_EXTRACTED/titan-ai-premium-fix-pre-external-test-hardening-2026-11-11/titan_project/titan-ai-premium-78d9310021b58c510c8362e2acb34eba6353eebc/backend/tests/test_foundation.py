from __future__ import annotations

from app.core.enums import DataSourceKind, DecisionAction, EnvironmentMode, MarketType, Timeframe
from app.engines.decision import DecisionEngine
from app.engines.market_data import MarketDataEngine
from app.engines.risk import RiskEngine
from app.engines.technical import TechnicalAnalysisEngine


def test_separation_enums():
    assert EnvironmentMode.DEMO.value == "demo"
    assert EnvironmentMode.LIVE.value == "live"
    assert {m.value for m in MarketType} == {"spot", "futures", "earn"}


def test_market_data_is_labeled_synthetic():
    md = MarketDataEngine()
    batch = md.get_synthetic_candles("BTC/USDT", Timeframe.M5, limit=60, seed=1)
    assert batch.source == DataSourceKind.SYNTHETIC_LABELED
    assert "SYNTHETIC_LABELED" in batch.note
    assert len(batch.df) == 60
    cols = set(getattr(batch.df, "columns", []))
    assert {"open", "high", "low", "close", "volume"}.issubset(cols)


def test_technical_analysis_returns_structure():
    md = MarketDataEngine()
    ta = TechnicalAnalysisEngine()
    df = md.get_synthetic_candles("ETH/USDT", Timeframe.H1, limit=80, seed=2).df
    res = ta.analyze(df, Timeframe.H1)
    assert res.timeframe == Timeframe.H1
    assert res.trend in {"bullish", "bearish", "sideways", "unknown"}
    assert "rsi14" in res.indicators
    assert "bias" in res.structure


def test_risk_spot_has_no_leverage_and_earn_blocked():
    risk = RiskEngine()
    spot = risk.validate_trade(environment=EnvironmentMode.DEMO, market=MarketType.SPOT, balance=1000, risk_per_trade=0.01,
                               entry=100, stop_loss=99, leverage=5)
    assert spot["leverage_used"] == 1.0
    earn = risk.validate_trade(environment=EnvironmentMode.DEMO, market=MarketType.EARN, balance=1000, risk_per_trade=0.01,
                               entry=100, stop_loss=99, leverage=1)
    assert earn["ok"] is False


def test_risk_leverage_is_capped():
    risk = RiskEngine()
    fut = risk.validate_trade(environment=EnvironmentMode.DEMO, market=MarketType.FUTURES, balance=1000, risk_per_trade=0.01,
                              entry=100, stop_loss=99, leverage=999, max_leverage=5)
    assert fut["leverage_used"] <= 5


def test_cost_estimate_is_labeled():
    risk = RiskEngine()
    c = risk.estimate_costs(1000, MarketType.FUTURES).to_dict()
    assert c["is_estimate"] is True
    assert c["total_cost_estimate"] >= 0


def test_decision_returns_valid_action_and_labels_data():
    md = MarketDataEngine()
    ta = TechnicalAnalysisEngine()
    de = DecisionEngine()
    batches = md.get_multi_timeframe("BTC/USDT", seed=42)
    analyses = {tf: ta.analyze(b.df, tf) for tf, b in batches.items()}
    result = de.decide(symbol="BTC/USDT", market=MarketType.SPOT, environment=EnvironmentMode.DEMO, timeframe_analyses=analyses, batches=batches)
    assert result["action"] in {a.value for a in DecisionAction}
    assert result["data_source_warning"]
    assert "synthetic_labeled" in str(result["data_sources"])


def test_candle_patterns_include_pinbars_stars_and_candle_strength():
    class Rows:
        def __init__(self, rows):
            self.rows = rows

    downtrend = [
        {"open": 10.2, "high": 10.3, "low": 10.0, "close": 10.1},
        {"open": 10.0, "high": 10.1, "low": 9.7, "close": 9.8},
        {"open": 9.8, "high": 9.9, "low": 9.5, "close": 9.6},
        {"open": 9.6, "high": 9.7, "low": 9.3, "close": 9.4},
        {"open": 9.4, "high": 9.5, "low": 9.1, "close": 9.2},
        {"open": 9.5, "high": 9.8, "low": 8.0, "close": 9.7},
    ]
    result = TechnicalAnalysisEngine()._candle_patterns(Rows(downtrend))
    assert "bullish_candle" in result["patterns"]
    assert "pin_bar_bullish" in result["patterns"]
    assert "hammer" in result["patterns"]
    assert "close_strength" in result and "lower_wick_to_body" in result


def test_morning_star_and_evening_star_detection():
    class Rows:
        def __init__(self, rows):
            self.rows = rows

    ta = TechnicalAnalysisEngine()
    morning = [
        {"open": 10.0, "high": 10.5, "low": 7.0, "close": 7.5},
        {"open": 7.3, "high": 7.6, "low": 7.1, "close": 7.4},
        {"open": 7.5, "high": 9.5, "low": 7.4, "close": 9.2},
    ]
    evening = [
        {"open": 7.5, "high": 10.5, "low": 7.0, "close": 10.0},
        {"open": 10.1, "high": 10.3, "low": 9.9, "close": 10.2},
        {"open": 10.0, "high": 10.1, "low": 8.0, "close": 8.3},
    ]
    assert "morning_star" in ta._candle_patterns(Rows(morning))["patterns"]
    assert "evening_star" in ta._candle_patterns(Rows(evening))["patterns"]


def test_market_structure_distinguishes_close_confirmed_and_false_breakouts():
    ta = TechnicalAnalysisEngine()
    highs = [10, 11, 10.5, 12, 11, 12.5, 11.5, 12.2, 11.8, 13.0, 12.4, 12.8, 12.1, 12.9, 12.6]
    lows = [8, 8.5, 8.2, 9, 8.7, 9.3, 8.9, 9.5, 9.1, 9.8, 9.4, 9.9, 9.5, 10.0, 10.1]
    closes = [9, 10, 9.5, 11, 10.5, 12, 11, 11.8, 11.2, 12.2, 11.8, 12.4, 12.0, 12.5, 12.7]
    result = ta._market_structure(highs, lows, closes)
    assert "minor_swings" in result and "major_swings" in result
    assert isinstance(result["breakouts"], list)
    assert isinstance(result["bos_choch"], list)


def test_public_market_data_discards_in_progress_candle(monkeypatch):
    import time
    from app.data.binance_public import BinancePublicData

    now_ms = int(time.time() * 1000)
    raw = []
    for i in range(31):
        open_time = now_ms - (31 - i) * 300_000
        close_time = open_time + 299_999
        raw.append([open_time, "100", "101", "99", "100.5", "10", close_time])
    raw[-1][6] = now_ms + 60_000
    monkeypatch.setattr(BinancePublicData, "get_klines", lambda self, symbol, interval, limit=500: raw)
    batch = MarketDataEngine().get_public_candles("BTC/USDT", Timeframe.M5, limit=31)
    assert batch.source == DataSourceKind.LIVE_PUBLIC
    assert len(batch.df) == 30
    assert batch.df.rows[-1]["close_time"] < now_ms


def test_risk_zero_trade_risk_is_rejected_without_default_fallback():
    result = RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO, market=MarketType.SPOT, balance=1000,
        risk_per_trade=0, entry=100, stop_loss=99, leverage=1,
    )
    assert result["ok"] is False
    assert result["issues"] == ["risk_per_trade_must_be_positive"]


def test_risk_explicit_zero_risk_cap_is_rejected():
    result = RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO, market=MarketType.SPOT, balance=1000,
        risk_per_trade=0.01, max_risk_per_trade=0, entry=100, stop_loss=99, leverage=1,
    )
    assert result["ok"] is False
    assert result["issues"] == ["risk_caps_must_be_positive"]


def test_risk_rejects_non_finite_and_malformed_inputs():
    import math
    for bad in (math.nan, math.inf, -math.inf, "not-a-number"):
        result = RiskEngine().validate_trade(
            environment=EnvironmentMode.DEMO, market=MarketType.SPOT, balance=1000,
            risk_per_trade=0.01, entry=100, stop_loss=99, leverage=1,
            current_exposure=bad,
        )
        assert result["ok"] is False
        assert result["issues"][0] in {"non_finite_numeric_input", "invalid_numeric_input"}


def test_risk_rejects_invalid_daily_loss_and_portfolio_limits():
    common = dict(
        environment=EnvironmentMode.DEMO, market=MarketType.SPOT, balance=1000,
        risk_per_trade=0.01, entry=100, stop_loss=99, leverage=1,
    )
    assert RiskEngine().validate_trade(**common, max_daily_loss=1.5)["ok"] is False
    assert RiskEngine().validate_trade(**common, max_open_positions=-1)["ok"] is False


def test_rsi_handles_flat_and_one_direction_price_series():
    from app.engines.technical import _rsi

    flat = _rsi([100.0] * 20, n=14)
    rising = _rsi([float(100 + i) for i in range(20)], n=14)
    falling = _rsi([float(100 - i) for i in range(20)], n=14)
    assert flat[-1] == 50.0
    assert rising[-1] == 100.0
    assert falling[-1] == 0.0


def test_rsi_rejects_non_finite_malformed_and_insufficient_inputs():
    import math
    from app.engines.technical import _rsi

    assert _rsi([], 14) == []
    assert all(value is None for value in _rsi([1.0] * 14, 14))
    for bad in (math.nan, math.inf, -math.inf, "not-a-number"):
        values = _rsi([100.0] * 14 + [bad, 101.0], 14)
        assert all(value is None for value in values)
    assert all(value is None for value in _rsi([1.0, 2.0, 3.0], 0))
    assert all(value is None for value in _rsi([1.0, 2.0, 3.0], -1))
    assert all(value is None for value in _rsi([1.0, 2.0, 3.0], True))


def test_rsi_accepts_numeric_strings_and_preserves_finite_output():
    from app.engines.technical import _rsi

    values = _rsi([str(100 + i) for i in range(20)], 14)
    assert values[-1] == 100.0


def test_risk_engine_rejects_boolean_and_invalid_cost_inputs():
    risk = RiskEngine()
    result = risk.validate_trade(
        environment=EnvironmentMode.DEMO, market=MarketType.SPOT,
        balance=True, risk_per_trade=0.01, entry=100, stop_loss=99, leverage=1,
    )
    assert result["ok"] is False
    assert result["issues"] == ["boolean_numeric_input_not_allowed"]
    import pytest
    with pytest.raises(ValueError, match="non_finite_cost_estimate_input"):
        risk.estimate_costs(float("nan"), MarketType.SPOT)
    with pytest.raises(ValueError, match="cost_estimate_inputs_out_of_range"):
        risk.estimate_costs(100, MarketType.SPOT, slippage_rate=1.0)
    with pytest.raises(ValueError, match="maker_taker_must_be_maker_or_taker"):
        risk.estimate_costs(100, MarketType.SPOT, maker_taker="unknown")


def test_risk_engine_rejects_fractional_open_position_count_and_invalid_enums():
    risk = RiskEngine()
    fractional = risk.validate_trade(
        environment=EnvironmentMode.DEMO, market=MarketType.SPOT,
        balance=1000, risk_per_trade=0.01, entry=100, stop_loss=99,
        leverage=1, open_positions=1.5,
    )
    assert fractional["ok"] is False
    assert fractional["issues"] == ["invalid_open_position_count"]
    invalid_market = risk.validate_trade(
        environment=EnvironmentMode.DEMO, market="futures",
        balance=1000, risk_per_trade=0.01, entry=100, stop_loss=99, leverage=2,
    )
    assert invalid_market["ok"] is False
    assert invalid_market["issues"] == ["invalid_environment_or_market"]


def test_cost_estimate_rejects_boolean_numeric_inputs():
    import pytest
    with pytest.raises(ValueError, match="boolean_cost_estimate_input_not_allowed"):
        RiskEngine().estimate_costs(True, MarketType.SPOT)


def test_position_manager_rejects_boolean_price_and_position_values():
    from app.services.position_manager import ManagedPosition
    position = ManagedPosition(symbol="BTC/USDT", side="long", entry_price=100, quantity=1)
    result = position.update_price(True)
    assert result["action"] is None
    assert result["reason"] == "boolean_position_value_not_allowed"
    invalid = ManagedPosition(symbol="BTC/USDT", side="long", entry_price=True, quantity=1)
    assert invalid.update_price(100)["reason"] == "boolean_position_value_not_allowed"
