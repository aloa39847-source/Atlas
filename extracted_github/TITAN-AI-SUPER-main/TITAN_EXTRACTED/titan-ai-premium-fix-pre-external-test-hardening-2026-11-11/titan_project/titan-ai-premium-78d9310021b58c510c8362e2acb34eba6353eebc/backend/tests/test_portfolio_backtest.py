from app.engines.strategy_backtest_suite import StrategyBacktestSuite
from app.engines.portfolio_backtest import PortfolioBacktestEngine


def _history():
    rows = []
    for index in range(70):
        price = 100 + index * 0.1
        rows.append({
            "timestamp": index * 3_600_000 + 1,
            "open": price, "high": price + 0.5,
            "low": price - 0.5, "close": price + 0.1,
            "volume": 1000,
        })
    return rows


def test_portfolio_engine_allocates_separate_sleeves_and_combines_concurrent_positions(monkeypatch):
    calls = []
    def fake_compare(self, candles, *, market_type, timeframe, strategy_keys,
                     initial_cash, fee_rate, slippage_rate, max_notional,
                     max_leverage, leverage, funding_events, **kwargs):
        symbol = "BTCUSDT" if candles[0]["close"] < 150 else "ETHUSDT"
        calls.append((symbol, initial_cash, max_notional, market_type))
        curve = [initial_cash] * len(candles)
        for index in range(10, len(curve)):
            curve[index] += 2
        for index in range(20, len(curve)):
            curve[index] += 2
        return {
            "ok": True,
            "leaderboard": [{
                "ok": True, "strategy_key": strategy_keys[0],
                "final_equity": initial_cash + 4,
                "total_return": 4 / initial_cash,
                "total_trades": 1, "fees_paid": 0.1, "slippage_cost": 0.05,
                "funding_net": 0.0, "liquidations": 0,
                "equity_curve": curve,
                "trades": [{
                    "entry_index": 10, "exit_index": 20,
                    "net_pnl": 4, "entry_price": 100, "exit_price": 104,
                }],
            }],
        }
    monkeypatch.setattr(StrategyBacktestSuite, "compare", fake_compare)
    histories = {"BTCUSDT": _history(), "ETHUSDT": _history()}
    result = PortfolioBacktestEngine().run(
        histories, market_type="spot", strategy_key="trend_following_ema",
        timeframe="1h", initial_cash=1000, max_positions=2,
        max_notional_per_position=80, fee_rate=0, slippage_rate=0,
    )
    assert result["ok"] is True
    assert result["initial_cash"] == 1000
    assert result["capital_per_symbol"] == 500
    assert result["symbols_succeeded"] == 2
    assert result["max_concurrent_positions_observed"] == 2
    assert result["final_equity"] == 1008
    assert result["total_trades"] == 2
    assert result["position_model"] == "one_net_position_per_symbol_strategy_sleeve_with_optional_bounded_pyramiding"
    assert all(call[1] == 500 for call in calls)


def test_portfolio_engine_caps_futures_notional_by_each_allocated_margin_sleeve(monkeypatch):
    seen = []
    def fake_compare(self, candles, *, market_type, timeframe, strategy_keys,
                     initial_cash, fee_rate, slippage_rate, max_notional,
                     max_leverage, leverage, funding_events, **kwargs):
        seen.append((initial_cash, max_notional, leverage))
        return {
            "ok": True,
            "leaderboard": [{
                "ok": True, "final_equity": initial_cash, "total_return": 0,
                "total_trades": 0, "fees_paid": 0, "slippage_cost": 0,
                "funding_net": 0, "liquidations": 0,
                "equity_curve": [initial_cash] * len(candles), "trades": [],
            }],
        }
    monkeypatch.setattr(StrategyBacktestSuite, "compare", fake_compare)
    result = PortfolioBacktestEngine().run(
        {"BTCUSDT": _history(), "ETHUSDT": _history()},
        market_type="futures", strategy_key="trend_following_ema",
        timeframe="1h", initial_cash=1000, max_positions=2,
        max_notional_per_position=800, leverage=3, max_leverage=5,
    )
    assert result["ok"] is True
    assert seen == [(500, 800, 3), (500, 800, 3)]


def test_portfolio_engine_rejects_unaligned_timestamps_and_excess_positions():
    first = _history()
    second = _history()
    for candle in second:
        candle["timestamp"] += 1
    engine = PortfolioBacktestEngine()
    unaligned = engine.run(
        {"BTCUSDT": first, "ETHUSDT": second},
        market_type="spot", strategy_key="trend_following_ema",
        timeframe="1h", max_positions=2,
    )
    excess = engine.run(
        {"BTCUSDT": first, "ETHUSDT": _history()},
        market_type="spot", strategy_key="trend_following_ema",
        timeframe="1h", max_positions=1,
    )
    assert unaligned["error"] == "portfolio_histories_need_60_common_timestamps"
    assert excess["error"] == "max_positions_must_be_between_2_and_60"


def test_portfolio_engine_supports_multiple_strategies_per_symbol_concurrently(monkeypatch):
    def fake_compare(self, candles, *, market_type, timeframe, strategy_keys,
                     initial_cash, fee_rate, slippage_rate, max_notional,
                     max_leverage, leverage, funding_events, **kwargs):
        curve = [initial_cash] * len(candles)
        for index in range(10, len(curve)):
            curve[index] += 2
        for index in range(20, len(curve)):
            curve[index] += 2
        return {
            "ok": True,
            "leaderboard": [{
                "ok": True, "strategy_key": strategy_keys[0],
                "final_equity": initial_cash + 4, "total_return": 4 / initial_cash,
                "total_trades": 1, "fees_paid": 0, "slippage_cost": 0,
                "funding_net": 0, "liquidations": 0, "equity_curve": curve,
                "trades": [{"entry_index": 10, "exit_index": 20, "net_pnl": 4}],
            }],
        }
    monkeypatch.setattr(StrategyBacktestSuite, "compare", fake_compare)
    result = PortfolioBacktestEngine().run(
        {"BTCUSDT": _history(), "ETHUSDT": _history()},
        market_type="spot", strategy_keys=["trend_following_ema", "momentum_macd_rsi"],
        timeframe="1h", initial_cash=1000, max_positions=4,
        max_notional_per_position=80, fee_rate=0, slippage_rate=0,
    )
    assert result["ok"] is True
    assert result["sleeves_requested"] == 4
    assert result["sleeves_succeeded"] == 4
    assert result["capital_per_sleeve"] == 250
    assert result["strategies_succeeded"] == 2
    assert result["max_concurrent_positions_observed"] == 4
    assert result["final_equity"] == 1016
    assert len(result["trades"]) == 4


def test_portfolio_engine_stress_tests_ten_symbols_and_six_strategies(monkeypatch):
    seen = []

    def fake_compare(self, candles, *, market_type, timeframe, strategy_keys,
                     initial_cash, fee_rate, slippage_rate, max_notional,
                     max_leverage, leverage, funding_events, **kwargs):
        seen.append((initial_cash, max_notional, market_type, strategy_keys[0]))
        curve = [initial_cash] * len(candles)
        for index in range(10, len(curve)):
            curve[index] += 2
        return {
            "ok": True,
            "leaderboard": [{
                "ok": True, "strategy_key": strategy_keys[0],
                "final_equity": initial_cash + 2,
                "total_return": 2 / initial_cash,
                "total_trades": 1, "fees_paid": 0.01,
                "slippage_cost": 0.01, "funding_net": 0.0,
                "liquidations": 0, "equity_curve": curve,
                "trades": [{
                    "entry_index": 10, "exit_index": 20,
                    "net_pnl": 2, "entry_price": 100, "exit_price": 102,
                }],
            }],
        }

    monkeypatch.setattr(StrategyBacktestSuite, "compare", fake_compare)
    symbols = [f"COIN{index}USDT" for index in range(10)]
    histories = {symbol: _history() for symbol in symbols}
    strategies = [
        "trend_following_ema", "momentum_macd_rsi", "mean_reversion_bbands",
        "breakout_structure", "scalping_m5", "swing_h1_h4",
    ]
    result = PortfolioBacktestEngine().run(
        histories, market_type="spot", strategy_keys=strategies,
        timeframe="1h", initial_cash=6000, max_positions=60,
        max_notional_per_position=80, fee_rate=0, slippage_rate=0,
    )
    assert result["ok"] is True
    assert result["symbols_tested"] == 10
    assert result["strategies_succeeded"] == 6
    assert result["sleeves_requested"] == 60
    assert result["sleeves_succeeded"] == 60
    assert result["capital_per_sleeve"] == 100
    assert result["max_concurrent_positions_observed"] == 60
    assert result["final_equity"] == 6120
    assert len(seen) == 60
    assert all(row[0] == 100 and row[1] == 80 and row[2] == "spot" for row in seen)


def test_portfolio_engine_supports_weighted_strategy_capital_allocation(monkeypatch):
    seen = []
    def fake_compare(self, candles, *, market_type, timeframe, strategy_keys,
                     initial_cash, fee_rate, slippage_rate, max_notional,
                     max_leverage, leverage, funding_events, **kwargs):
        seen.append((strategy_keys[0], initial_cash, max_notional))
        return {"ok": True, "leaderboard": [{
            "ok": True, "final_equity": initial_cash, "total_return": 0,
            "total_trades": 0, "fees_paid": 0, "slippage_cost": 0,
            "funding_net": 0, "liquidations": 0,
            "equity_curve": [initial_cash] * len(candles), "trades": [],
        }]}
    monkeypatch.setattr(StrategyBacktestSuite, "compare", fake_compare)
    strategies = ["trend_following_ema", "momentum_macd_rsi"]
    result = PortfolioBacktestEngine().run(
        {"BTCUSDT": _history(), "ETHUSDT": _history()},
        market_type="spot", strategy_keys=strategies, timeframe="1h",
        initial_cash=1000, max_positions=4, max_notional_per_position=80,
        strategy_allocation_weights={"trend_following_ema": 3, "momentum_macd_rsi": 1},
    )
    assert result["ok"] is True
    assert result["strategy_allocation_weights"] == {
        "trend_following_ema": 0.75, "momentum_macd_rsi": 0.25
    }
    assert result["capital_allocation_method"] == "strategy_weighted_symbol_equal"
    assert sorted(seen) == [
        ("momentum_macd_rsi", 125.0, 80.0),
        ("momentum_macd_rsi", 125.0, 80.0),
        ("trend_following_ema", 375.0, 80.0),
        ("trend_following_ema", 375.0, 80.0),
    ]
    assert result["per_symbol"]["BTCUSDT"]["trend_following_ema"]["capital_allocated"] == 375.0
    assert result["per_symbol"]["BTCUSDT"]["momentum_macd_rsi"]["capital_allocated"] == 125.0


def test_portfolio_engine_rejects_invalid_strategy_allocation_weights():
    engine = PortfolioBacktestEngine()
    histories = {"BTCUSDT": _history(), "ETHUSDT": _history()}
    result = engine.run(
        histories, market_type="spot", strategy_key="trend_following_ema",
        timeframe="1h", max_positions=2,
        strategy_allocation_weights={"trend_following_ema": 0},
    )
    assert result["ok"] is False
    assert result["error"] == "strategy_allocation_weight_must_be_positive_finite"
