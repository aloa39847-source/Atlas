from app.engines.futures_backtest import FuturesBacktestEngine


def _candles():
    return [
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100},
        {"timestamp": 2000, "open": 100, "high": 110, "low": 95, "close": 105},
        {"timestamp": 3000, "open": 105, "high": 110, "low": 104, "close": 108},
        {"timestamp": 4000, "open": 108, "high": 111, "low": 106, "close": 109},
    ]


def test_futures_backtest_models_long_short_and_next_candle_fills():
    engine = FuturesBacktestEngine(initial_cash=1000, max_notional=800, max_leverage=10)
    long_result = engine.run(
        _candles(),
        [{"index": 0, "action": "long", "leverage": 2, "notional": 400},
         {"index": 1, "action": "close"}],
    )
    short_result = engine.run(
        _candles(),
        [{"index": 0, "action": "short", "leverage": 2, "notional": 400},
         {"index": 1, "action": "close"}],
    )
    assert long_result["ok"] is True
    assert short_result["ok"] is True
    assert long_result["trades"][0]["entry_index"] == 1
    assert long_result["trades"][0]["exit_index"] == 2
    assert long_result["trades"][0]["net_pnl"] > 0
    assert short_result["trades"][0]["net_pnl"] < 0
    assert long_result["no_lookahead"] is False
    assert long_result["signal_causality"] == "not_verified_for_external_signals"
    assert long_result["execution"] == "none"


def test_futures_backtest_applies_funding_only_while_position_is_open():
    engine = FuturesBacktestEngine(initial_cash=1000, max_notional=800)
    result = engine.run(
        _candles(),
        [{"index": 0, "action": "long", "leverage": 2, "notional": 400},
         {"index": 1, "action": "close"}],
        funding_events=[{"timestamp": 2500, "rate": 0.01}],
    )
    assert result["ok"] is True
    assert result["funding_net"] < 0
    assert result["trades"][0]["funding_net_allocated"] < 0


def test_futures_backtest_supports_partial_entry_and_partial_exit_fills():
    engine = FuturesBacktestEngine(initial_cash=1000, max_notional=800)
    result = engine.run(
        _candles(),
        [{"index": 0, "action": "long", "leverage": 2, "notional": 400, "fill_ratio": 0.5},
         {"index": 1, "action": "reduce", "fill_ratio": 0.5}],
    )
    assert result["ok"] is True
    assert result["partial_fills"] == 2
    assert len(result["trades"]) == 2
    assert result["trades"][0]["exit_reason"] == "partial_reduce"
    assert result["trades"][1]["exit_reason"] == "end_of_data"


def test_futures_backtest_estimates_liquidation_for_long_and_short():
    candles = [
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100},
        {"timestamp": 2000, "open": 100, "high": 115, "low": 85, "close": 90},
        {"timestamp": 3000, "open": 90, "high": 92, "low": 88, "close": 90},
    ]
    engine = FuturesBacktestEngine(initial_cash=1000, max_notional=800, max_leverage=10)
    long_result = engine.run(candles, [{"index": 0, "action": "long", "leverage": 10, "notional": 800}])
    short_result = engine.run(candles, [{"index": 0, "action": "short", "leverage": 10, "notional": 800}])
    assert long_result["ok"] is True and long_result["liquidations"] == 1
    assert short_result["ok"] is True and short_result["liquidations"] == 1
    assert long_result["trades"][0]["exit_reason"] == "estimated_liquidation"
    assert short_result["trades"][0]["exit_reason"] == "estimated_liquidation"


def test_futures_backtest_enforces_800_usdt_notional_cap_and_validates_inputs():
    engine = FuturesBacktestEngine(initial_cash=1000, max_notional=800)
    capped = engine.run(
        _candles(), [{"index": 0, "action": "long", "leverage": 10, "notional": 5000}],
    )
    assert capped["ok"] is True
    assert capped["trades"][0]["quantity"] * capped["trades"][0]["entry_price"] <= 800.01

    invalid_funding = engine.run(
        _candles(), [], funding_events=[{"timestamp": 2500, "rate": True}],
    )
    assert invalid_funding["ok"] is False
    assert invalid_funding["error"] == "invalid_funding_event"


def test_futures_backtest_rejects_non_monotonic_timestamps_and_bad_configuration():
    engine = FuturesBacktestEngine()
    candles = _candles()
    candles[2]["timestamp"] = candles[1]["timestamp"]
    assert engine.run(candles, [])["error"] == "candle_timestamps_must_increase"
    try:
        FuturesBacktestEngine(max_notional=float("inf"))
    except ValueError as exc:
        assert str(exc) == "non_finite_futures_backtest_configuration"
    else:
        raise AssertionError("non-finite configuration must be rejected")


def test_futures_backtest_generates_causal_signals_from_existing_strategy():
    candles = []
    previous = 100.0
    for index in range(100):
        close = previous + 0.5
        candles.append({
            "timestamp": (index + 1) * 3_600_000,
            "open": previous, "high": close + 0.5,
            "low": previous - 0.5, "close": close, "volume": 1000.0,
        })
        previous = close
    result = FuturesBacktestEngine(initial_cash=1000, max_notional=800).run_strategy(
        candles, strategy_key="trend_following_ema", timeframe="1h",
        leverage=2, notional=400,
    )
    assert result["ok"] is True
    assert result["strategy_key"] == "trend_following_ema"
    assert result["signal_generation"] == "technical_analysis_on_causal_history_prefix"
    assert result["no_lookahead"] is True


def test_futures_backtest_strategy_enforces_timeframe_contracts():
    candles = _candles() * 20
    result = FuturesBacktestEngine().run_strategy(
        candles, strategy_key="scalping_m5", timeframe="1h",
    )
    assert result["ok"] is False
    assert result["error"] == "scalping_m5_requires_5m_timeframe"


def test_futures_liquidation_models_adverse_gap_at_open():
    candles = [
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100},
        {"timestamp": 2000, "open": 100, "high": 101, "low": 99, "close": 100},
        {"timestamp": 3000, "open": 80, "high": 90, "low": 75, "close": 85},
        {"timestamp": 4000, "open": 85, "high": 86, "low": 84, "close": 85},
    ]
    result = FuturesBacktestEngine(
        initial_cash=1000, max_notional=800, max_leverage=10,
        slippage_rate=0.001,
    ).run(candles, [{"index": 0, "action": "long", "leverage": 10, "notional": 800}])
    assert result["ok"] is True
    assert result["liquidations"] == 1
    assert result["trades"][0]["exit_reason"] == "estimated_liquidation"
    assert result["trades"][0]["exit_price"] < 80


def test_futures_liquidation_threshold_solves_maintenance_equation_independently_of_close_mark():
    engine = FuturesBacktestEngine(
        initial_cash=1000, max_notional=800, max_leverage=10,
        fee_rate=0.0, slippage_rate=0.0, maintenance_margin_rate=0.01,
    )
    long_candles = [
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100},
        {"timestamp": 2000, "open": 100, "high": 101, "low": 90, "close": 95},
        {"timestamp": 3000, "open": 95, "high": 96, "low": 94, "close": 95},
    ]
    result = engine.run(
        long_candles,
        [{"index": 0, "action": "long", "leverage": 10, "notional": 800}],
    )
    assert result["ok"] is True
    assert result["liquidations"] == 1
    # Initial margin is 80 USDT; qty is 8 BTC; maintenance is 1%.
    # Exact long threshold: (100 - 80/8) / (1 - 0.01) = 90.90909...
    assert abs(result["trades"][0]["exit_price"] - 90.9090909) < 0.01

    short_candles = [
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100},
        {"timestamp": 2000, "open": 100, "high": 110, "low": 99, "close": 105},
        {"timestamp": 3000, "open": 105, "high": 106, "low": 104, "close": 105},
    ]
    short = engine.run(
        short_candles,
        [{"index": 0, "action": "short", "leverage": 10, "notional": 800}],
    )
    assert short["ok"] is True
    assert short["liquidations"] == 1
    # Exact short threshold: (100 + 80/8) / (1 + 0.01) = 108.91089...
    assert abs(short["trades"][0]["exit_price"] - 108.9108911) < 0.01


def test_futures_pyramiding_adds_same_side_with_weighted_entry_and_total_cap():
    engine = FuturesBacktestEngine(
        initial_cash=1000, max_notional=800, max_leverage=10,
        fee_rate=0, slippage_rate=0,
    )
    candles = [
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100},
        {"timestamp": 2000, "open": 110, "high": 111, "low": 109, "close": 110},
        {"timestamp": 3000, "open": 120, "high": 121, "low": 119, "close": 120},
        {"timestamp": 4000, "open": 130, "high": 131, "low": 129, "close": 130},
        {"timestamp": 5000, "open": 140, "high": 141, "low": 139, "close": 140},
    ]
    result = engine.run(
        candles,
        [
            {"index": 0, "action": "long", "leverage": 2, "notional": 533.333333},
            {"index": 1, "action": "long", "add_position": True, "leverage": 2, "notional": 133.333333},
            {"index": 2, "action": "close"},
        ],
        pyramiding_enabled=True, max_adds=2,
    )
    assert result["ok"] is True
    assert result["total_trades"] == 1
    trade = result["trades"][0]
    assert trade["add_count"] == 1
    assert len(trade["add_entries"]) == 1
    assert trade["quantity"] * trade["entry_price"] <= 800.0 + 1e-6
    assert trade["entry_index"] == 1
    assert trade["exit_index"] == 3
