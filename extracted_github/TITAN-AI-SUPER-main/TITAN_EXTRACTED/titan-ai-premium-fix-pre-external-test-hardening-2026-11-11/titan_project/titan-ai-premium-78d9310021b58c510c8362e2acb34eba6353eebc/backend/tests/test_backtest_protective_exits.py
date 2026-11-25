from app.engines.backtest import BacktestEngine
from app.engines.futures_backtest import FuturesBacktestEngine


def _spot_candles(*, low=99, high=104, open_price=100):
    return [
        {"open": 100, "high": 101, "low": 99, "close": 100},
        {"open": open_price, "high": high, "low": low, "close": max(low, min(high, 100))},
        {"open": 100, "high": 101, "low": 99, "close": 100},
    ]


def _futures_candles(*, low=99, high=104, open_price=100):
    return [
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100},
        {"timestamp": 2000, "open": open_price, "high": high, "low": low, "close": 100},
        {"timestamp": 3000, "open": 100, "high": 101, "low": 99, "close": 100},
    ]


def test_spot_backtest_executes_intrabar_stop_loss_and_models_gap_slippage():
    engine = BacktestEngine(
        fee_rate=0, slippage_rate=0, initial_cash=1000,
        allocation_fraction=1, max_notional=500,
    )
    gap_candles = [
        {"open": 100, "high": 101, "low": 99, "close": 100},
        {"open": 100, "high": 104, "low": 99, "close": 102},
        {"open": 90, "high": 96, "low": 88, "close": 92},
        {"open": 92, "high": 93, "low": 91, "close": 92},
    ]
    result = engine.run(
        gap_candles,
        [{"index": 0, "action": "buy", "stop_distance": 5, "target_distance": 10}],
    )
    assert result["ok"] is True
    assert result["total_trades"] == 1
    assert result["trades"][0]["exit_reason"] == "stop_loss"
    assert result["trades"][0]["exit_price"] == 90


def test_spot_backtest_uses_stop_first_when_both_protective_levels_hit():
    engine = BacktestEngine(
        fee_rate=0, slippage_rate=0, initial_cash=1000,
        allocation_fraction=1, max_notional=500,
    )
    result = engine.run(
        _spot_candles(low=94, high=111),
        [{"index": 0, "action": "buy", "stop_distance": 5, "target_distance": 10}],
    )
    assert result["ok"] is True
    assert result["trades"][0]["exit_reason"] == "stop_loss"
    assert result["trades"][0]["exit_price"] == 95


def test_spot_backtest_executes_take_profit_at_limit_price():
    engine = BacktestEngine(
        fee_rate=0, slippage_rate=0, initial_cash=1000,
        allocation_fraction=1, max_notional=500,
    )
    result = engine.run(
        _spot_candles(low=99, high=111),
        [{"index": 0, "action": "buy", "stop_distance": 5, "target_distance": 10}],
    )
    assert result["ok"] is True
    assert result["trades"][0]["exit_reason"] == "take_profit"
    assert result["trades"][0]["exit_price"] == 110


def test_futures_backtest_models_long_stop_and_short_target():
    engine = FuturesBacktestEngine(
        initial_cash=1000, max_notional=800, max_leverage=5,
        fee_rate=0, slippage_rate=0,
    )
    long_result = engine.run(
        _futures_candles(low=94, high=104),
        [{"index": 0, "action": "long", "leverage": 1, "notional": 100,
          "stop_distance": 5, "target_distance": 10}],
    )
    short_result = engine.run(
        _futures_candles(low=89, high=104),
        [{"index": 0, "action": "short", "leverage": 1, "notional": 100,
          "stop_distance": 5, "target_distance": 10}],
    )
    assert long_result["ok"] is True
    assert long_result["trades"][0]["exit_reason"] == "stop_loss"
    assert long_result["trades"][0]["exit_price"] == 95
    assert short_result["ok"] is True
    assert short_result["trades"][0]["exit_reason"] == "take_profit"
    assert short_result["trades"][0]["exit_price"] == 90


def test_futures_backtest_uses_stop_first_when_both_levels_hit():
    engine = FuturesBacktestEngine(
        initial_cash=1000, max_notional=800, max_leverage=5,
        fee_rate=0, slippage_rate=0,
    )
    result = engine.run(
        _futures_candles(low=94, high=111),
        [{"index": 0, "action": "long", "leverage": 1, "notional": 100,
          "stop_distance": 5, "target_distance": 10}],
    )
    assert result["ok"] is True
    assert result["trades"][0]["exit_reason"] == "stop_loss"
    assert result["trades"][0]["exit_price"] == 95


def test_protective_exit_distances_reject_invalid_values():
    spot = BacktestEngine().run(
        _spot_candles(),
        [{"index": 0, "action": "buy", "stop_distance": float("nan")}],
    )
    futures = FuturesBacktestEngine().run(
        _futures_candles(),
        [{"index": 0, "action": "long", "stop_distance": True}],
    )
    assert spot["ok"] is False
    assert spot["error"] == "protective_exit_distance_out_of_range"
    assert futures["ok"] is False
    assert futures["error"] == "boolean_protective_exit_distance_not_allowed"
