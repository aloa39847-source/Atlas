from app.engines.strategy_backtest_suite import StrategyBacktestSuite, CANDLE_STRATEGIES


def _candles(count=120):
    rows = []
    previous = 100.0
    for index in range(count):
        close = previous + 0.35
        rows.append({
            "timestamp": (index + 1) * 3_600_000,
            "open": previous,
            "high": close + 0.4,
            "low": previous - 0.4,
            "close": close,
            "volume": 1000.0,
        })
        previous = close
    return rows


def test_strategy_suite_compares_all_candle_strategies_on_spot():
    result = StrategyBacktestSuite().compare(
        _candles(), market_type="spot", timeframe="1h",
        initial_cash=150, fee_rate=0, slippage_rate=0,
        max_notional=80,
    )
    assert result["ok"] is True
    assert result["strategies_tested"] == len(CANDLE_STRATEGIES)
    assert result["strategies_succeeded"] == len(CANDLE_STRATEGIES) - 1
    assert {item["strategy_key"] for item in result["leaderboard"]} == set(CANDLE_STRATEGIES) - {"scalping_m5"}
    scalp = next(item for item in result["results"] if item["strategy_key"] == "scalping_m5")
    assert scalp["status"] == "skipped_incompatible_timeframe"
    assert result["execution"] == "simulated_only"
    assert result["no_live_orders"] is True
    assert result["specialized_strategies_not_run_on_candles"] == ["liquidity_orderflow", "arbitrage"]


def test_strategy_suite_supports_futures_with_funding_events():
    result = StrategyBacktestSuite().compare(
        _candles(), market_type="futures", timeframe="1h",
        strategy_keys=["trend_following_ema"], initial_cash=1000,
        fee_rate=0, slippage_rate=0, max_notional=800,
        max_leverage=10, leverage=2,
        funding_events=[],
    )
    assert result["ok"] is True
    assert result["market_type"] == "futures"
    assert result["leaderboard"][0]["strategy_key"] == "trend_following_ema"


def test_strategy_suite_refuses_to_fake_specialized_strategies_from_candles():
    result = StrategyBacktestSuite().compare(
        _candles(), market_type="spot", timeframe="1h",
        strategy_keys=["liquidity_orderflow"],
    )
    assert result["ok"] is False
    assert result["error"] == "strategy_requires_its_own_historical_data_type"
    assert result["specialized_strategies"] == ["liquidity_orderflow", "arbitrage"]


def test_strategy_suite_rejects_duplicate_or_invalid_strategy_lists():
    suite = StrategyBacktestSuite()
    duplicate = suite.compare(
        _candles(), market_type="spot", timeframe="1h",
        strategy_keys=["trend_following_ema", "trend_following_ema"],
    )
    invalid = suite.compare(
        _candles(), market_type="margin", timeframe="1h",
    )
    assert duplicate["error"] == "invalid_strategy_key_list"
    assert invalid["error"] == "market_type_must_be_spot_or_futures"
