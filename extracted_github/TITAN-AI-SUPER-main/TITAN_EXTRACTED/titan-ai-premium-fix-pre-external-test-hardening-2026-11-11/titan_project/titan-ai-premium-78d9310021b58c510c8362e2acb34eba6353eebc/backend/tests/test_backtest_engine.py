from app.engines.backtest import BacktestEngine, normalize_strategy_params


def _candles(closes):
    return [{"open": p, "high": p * 1.01, "low": p * 0.99, "close": p} for p in closes]


def test_backtest_fills_next_candle_and_accounts_for_entry_exit_costs():
    candles = _candles([100, 100, 110, 120])
    result = BacktestEngine(fee_rate=0.001, slippage_rate=0.0, initial_cash=1000).run(
        candles, [{"index": 0, "action": "buy"}, {"index": 2, "action": "sell"}]
    )
    assert result["total_trades"] == 1
    trade = result["trades"][0]
    assert trade["entry_index"] == 1
    assert trade["exit_index"] == 3
    assert trade["entry_price"] == 100
    assert trade["exit_price"] == 120
    assert trade["fees"] > 0
    assert result["walk_forward_ready"] is False
    assert result["out_of_sample_ready"] is False


def test_backtest_uses_end_of_data_exit_and_does_not_invent_fixed_profit():
    result = BacktestEngine(fee_rate=0, slippage_rate=0, initial_cash=1000).run(
        _candles([100, 100, 90]), [{"index": 0, "action": "buy"}]
    )
    assert result["total_trades"] == 1
    assert result["trades"][0]["exit_reason"] == "end_of_data"
    assert result["trades"][0]["net_pnl"] < 0
    assert result["total_return"] < 0


def test_backtest_requires_signal_index_and_valid_ohlc():
    engine = BacktestEngine()
    assert engine.run(_candles([100, 101]), [{"action": "buy", "price": 100}])["ok"] is False
    bad = [{"open": 100, "high": 90, "low": 95, "close": 98}, _candles([100])[0]]
    assert engine.run(bad, [])["ok"] is False


def test_backtest_is_spot_long_only_and_ignores_sell_without_position():
    result = BacktestEngine(fee_rate=0, slippage_rate=0, initial_cash=1000).run(
        _candles([100, 105, 110]), [{"index": 0, "action": "sell"}]
    )
    assert result["total_trades"] == 0
    assert result["total_return"] == 0


def test_walk_forward_runs_non_overlapping_oos_folds_without_future_candles():
    candles = _candles([100, 101, 102, 103, 104, 105, 106, 107, 108, 109])
    fit_ranges = []
    observed_histories = []

    def fit_fn(training):
        fit_ranges.append([row["close"] for row in training])
        return {"trained_through": training[-1]["close"]}

    def signal_fn(history, model):
        observed_histories.append([row["close"] for row in history])
        assert history[-1]["close"] >= model["trained_through"]
        if len(history) == 5:
            return "buy"
        if len(history) == 6:
            return "sell"
        return "wait"

    result = BacktestEngine(fee_rate=0, slippage_rate=0, initial_cash=1000).walk_forward(
        candles, signal_fn=signal_fn, fit_fn=fit_fn,
        train_window=4, test_window=3, step=3,
    )

    assert result["ok"] is True
    assert result["validation"] == "rolling_walk_forward_out_of_sample"
    assert result["fold_count"] == 2
    assert result["walk_forward_ready"] is True
    assert result["out_of_sample_ready"] is True
    assert result["no_lookahead"] is True
    assert result["model_fitted_per_fold"] is True
    assert fit_ranges == [[100, 101, 102, 103], [103, 104, 105, 106]]
    assert observed_histories[0] == [100, 101, 102, 103, 104]
    assert observed_histories[1] == [100, 101, 102, 103, 104, 105]
    assert observed_histories[2] == [103, 104, 105, 106, 107]


def test_walk_forward_rejects_overlapping_oos_windows_and_invalid_callbacks():
    engine = BacktestEngine()
    candles = _candles([100, 101, 102, 103, 104, 105])
    overlap = engine.walk_forward(
        candles, signal_fn=lambda history, model: "wait",
        train_window=3, test_window=2, step=1,
    )
    assert overlap["ok"] is False
    assert overlap["error"] == "step_must_be_at_least_test_window_to_avoid_overlapping_oos_folds"

    invalid = engine.walk_forward(
        candles, signal_fn=None, train_window=3, test_window=2,
    )
    assert invalid["ok"] is False
    assert invalid["error"] == "signal_fn_must_be_callable"


def _trend_candles(count=120, direction=1):
    rows = []
    previous = 100.0
    for i in range(count):
        close = 100.0 + direction * i * 0.35 + (0.2 if i % 2 else -0.2)
        open_price = previous
        rows.append({
            "open": open_price,
            "high": max(open_price, close) + 0.5,
            "low": min(open_price, close) - 0.5,
            "close": close,
            "volume": 1000.0 + i,
        })
        previous = close
    return rows


def test_backtest_engine_runs_existing_strategy_with_causal_history_prefix():
    engine = BacktestEngine(fee_rate=0, slippage_rate=0, initial_cash=150)
    result = engine.run_strategy(
        _trend_candles(100), strategy_key="trend_following_ema", timeframe="1h",
    )
    assert result["strategy_key"] == "trend_following_ema"
    assert result["signal_generation"] == "technical_analysis_on_causal_history_prefix"
    assert result["no_lookahead"] is True
    assert result["walk_forward_ready"] is False
    assert result["total_trades"] >= 1
    assert result["position_mode"] == "spot_long_only_single_position"


def test_backtest_engine_runs_strategy_walk_forward_oos_on_disjoint_folds():
    engine = BacktestEngine(fee_rate=0, slippage_rate=0, initial_cash=150)
    result = engine.run_strategy_walk_forward(
        _trend_candles(120), strategy_key="trend_following_ema",
        timeframe="1h", train_window=60, test_window=30, step=30,
    )
    assert result["ok"] is True
    assert result["strategy_key"] == "trend_following_ema"
    assert result["fold_count"] == 2
    assert result["out_of_sample_ready"] is True
    assert result["no_lookahead"] is True
    assert result["model_fitted_per_fold"] is False
    assert result["signal_generation"] == "existing_technical_strategy_on_causal_fold_history"


def test_backtest_engine_enforces_strategy_timeframe_contracts():
    engine = BacktestEngine()
    wrong_scalp_tf = engine.run_strategy(
        _trend_candles(100), strategy_key="scalping_m5", timeframe="1h",
    )
    assert wrong_scalp_tf["ok"] is False
    assert wrong_scalp_tf["error"] == "scalping_m5_requires_5m_timeframe"

    wrong_swing_tf = engine.run_strategy(
        _trend_candles(100), strategy_key="swing_h1_h4", timeframe="5m",
    )
    assert wrong_swing_tf["ok"] is False
    assert wrong_swing_tf["error"] == "swing_h1_h4_requires_1h_or_4h_timeframe"

    scalp = engine.run_strategy(
        _trend_candles(100), strategy_key="scalping_m5", timeframe="5m",
    )
    swing = engine.run_strategy(
        _trend_candles(100), strategy_key="swing_h1_h4", timeframe="4h",
    )
    assert scalp["strategy_key"] == "scalping_m5"
    assert swing["strategy_key"] == "swing_h1_h4"
    assert scalp["no_lookahead"] is True
    assert swing["no_lookahead"] is True


def test_backtest_engine_rejects_non_finite_configuration_and_malformed_containers():
    import pytest

    with pytest.raises(ValueError, match="non_finite_backtest_configuration"):
        BacktestEngine(fee_rate=float("nan"))
    with pytest.raises(ValueError, match="fee_and_slippage_must_be_between_zero_and_one"):
        BacktestEngine(slippage_rate=1.0)
    engine = BacktestEngine()
    assert engine.run(None, [])["error"] == "candles_and_signals_must_be_lists"
    assert engine.run([], None)["error"] == "candles_and_signals_must_be_lists"


def test_backtest_rejects_boolean_configuration_and_fractional_signal_indices():
    import pytest
    with pytest.raises(ValueError, match="boolean_backtest_configuration_not_allowed"):
        BacktestEngine(initial_cash=True)
    result = BacktestEngine().run(
        _candles([100, 101, 102]),
        [{"index": 0.5, "action": "buy"}],
    )
    assert result["ok"] is False
    assert result["error"] == "invalid_signal_index"


def test_backtest_rejects_boolean_ohlc_and_conflicting_same_candle_signals():
    candles = _candles([100, 101, 102])
    candles[0]["open"] = True
    assert BacktestEngine().run(candles, [])["error"] == "invalid_ohlc_at_index_0"
    conflict = BacktestEngine().run(
        _candles([100, 101, 102]),
        [{"index": 0, "action": "buy"}, {"index": 0, "action": "sell"}],
    )
    assert conflict["ok"] is False
    assert conflict["error"] == "conflicting_signals_same_candle"


def test_walk_forward_supports_implemented_scalping_and_swing_strategies():
    engine = BacktestEngine(fee_rate=0, slippage_rate=0, initial_cash=150)
    candles = _trend_candles(120)
    scalp = engine.run_strategy_walk_forward(
        candles, strategy_key="scalping_m5", timeframe="5m",
        train_window=60, test_window=30, step=30,
    )
    swing = engine.run_strategy_walk_forward(
        candles, strategy_key="swing_h1_h4", timeframe="4h",
        train_window=60, test_window=30, step=30,
    )
    assert scalp.get("error") != "unsupported_or_not_implemented_strategy"
    assert swing.get("error") != "unsupported_or_not_implemented_strategy"
    assert scalp.get("strategy_key") == "scalping_m5"
    assert swing.get("strategy_key") == "swing_h1_h4"
    assert scalp.get("no_lookahead") is True
    assert swing.get("no_lookahead") is True


def test_spot_backtest_enforces_eighty_usdt_per_trade_notional_cap():
    engine = BacktestEngine(
        fee_rate=0, slippage_rate=0, initial_cash=1000,
        allocation_fraction=1.0, max_notional=80,
    )
    result = engine.run(
        _candles([100, 100, 110, 120]),
        [{"index": 0, "action": "buy"}, {"index": 2, "action": "sell"}],
    )
    assert result["total_trades"] == 1
    trade = result["trades"][0]
    assert trade["quantity"] * trade["entry_price"] <= 80.0
    assert result["max_trade_notional"] == 80.0


def test_spot_backtest_rejects_invalid_trade_notional_cap():
    import pytest
    with pytest.raises(ValueError, match="invalid_backtest_capital_or_allocation"):
        BacktestEngine(max_notional=0)
    with pytest.raises(ValueError, match="boolean_backtest_configuration_not_allowed"):
        BacktestEngine(max_notional=True)


def test_spot_pyramiding_adds_to_one_net_position_within_total_notional_cap():
    engine = BacktestEngine(
        fee_rate=0, slippage_rate=0, initial_cash=1000,
        allocation_fraction=1.0, max_notional=80,
    )
    result = engine.run(
        _candles([100, 110, 120, 130, 140]),
        [
            {"index": 0, "action": "buy"},
            {"index": 1, "action": "add"},
            {"index": 2, "action": "sell"},
        ],
        pyramiding_enabled=True, max_adds=2, add_fraction=0.25,
    )
    assert result["ok"] is True
    assert result["total_trades"] == 1
    trade = result["trades"][0]
    assert trade["add_count"] == 1
    assert len(trade["add_entries"]) == 1
    assert trade["quantity"] * trade["entry_price"] <= 80.0 + 1e-8
    assert result["pyramiding_enabled"] is True


def test_strategy_parameter_schema_rejects_unknown_and_unsafe_values():
    assert normalize_strategy_params("trend_following_ema", {"min_confirmations": 1}) == {
        "ok": True, "params": {"min_confirmations": 1}
    }
    assert normalize_strategy_params("trend_following_ema", {"min_confirmations": True})["ok"] is False
    assert normalize_strategy_params("trend_following_ema", {"fast": 9})["error"] == "unsupported_strategy_parameter"
    assert normalize_strategy_params("momentum_macd_rsi", {
        "rsi_buy_min": 60, "rsi_buy_max": 55,
    })["error"] == "strategy_rsi_parameter_ranges_must_be_ordered"
    assert normalize_strategy_params("breakout_structure", {
        "bullish_close_strength": 0.4,
    })["ok"] is False


def test_strategy_parameters_change_signal_rules_without_mutating_defaults():
    from types import SimpleNamespace

    analysis = SimpleNamespace(
        trend="bullish",
        signals={"ema9_above_ema21"},
        indicators={}, structure={}, candles={},
        timeframe=SimpleNamespace(value="1h"),
    )
    assert BacktestEngine._strategy_action("trend_following_ema", analysis) is None
    assert BacktestEngine._strategy_action(
        "trend_following_ema", analysis, {"min_confirmations": 1},
    ) == "buy"
    assert BacktestEngine._strategy_action("trend_following_ema", analysis) is None


def test_momentum_rsi_parameters_are_used_in_historical_signal_generation():
    from types import SimpleNamespace

    analysis = SimpleNamespace(
        trend="bullish",
        signals={"macd_hist_positive"},
        indicators={"rsi14": 56.0, "stoch_rsi_k": None, "cci20": None},
        structure={}, candles={}, timeframe=SimpleNamespace(value="1h"),
    )
    assert BacktestEngine._strategy_action("momentum_macd_rsi", analysis) == "buy"
    assert BacktestEngine._strategy_action(
        "momentum_macd_rsi", analysis,
        {"rsi_buy_min": 60, "rsi_buy_max": 75, "rsi_sell_min": 25, "rsi_sell_max": 45},
    ) is None


def test_strategy_walk_forward_returns_the_exact_validated_parameter_snapshot():
    engine = BacktestEngine(fee_rate=0, slippage_rate=0, initial_cash=150)
    params = {"min_confirmations": 3}
    result = engine.run_strategy_walk_forward(
        _trend_candles(210), strategy_key="trend_following_ema",
        timeframe="1h", train_window=90, test_window=40, step=40,
        strategy_params=params,
    )
    assert result["ok"] is True
    assert result["strategy_params"] == {"min_confirmations": 3}
    assert result["no_lookahead"] is True
    invalid = engine.run_strategy_walk_forward(
        _trend_candles(210), strategy_key="trend_following_ema",
        timeframe="1h", strategy_params={"fast": 10},
    )
    assert invalid["ok"] is False
    assert invalid["error"] == "unsupported_strategy_parameter"
