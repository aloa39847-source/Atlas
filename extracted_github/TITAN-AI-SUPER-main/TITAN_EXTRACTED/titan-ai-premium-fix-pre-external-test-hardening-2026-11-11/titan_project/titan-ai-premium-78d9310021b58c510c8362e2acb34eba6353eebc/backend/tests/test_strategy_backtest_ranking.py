from app.engines.backtest import BacktestEngine
from app.engines.strategy_backtest_suite import StrategyBacktestSuite


def test_strategy_leaderboard_uses_lower_drawdown_as_first_tie_break(monkeypatch):
    metrics = {
        "trend_following_ema": {
            "total_return": 0.20, "max_drawdown": 0.30,
            "profit_factor": 2.0, "expectancy": 4.0, "total_trades": 10,
        },
        "momentum_macd_rsi": {
            "total_return": 0.20, "max_drawdown": 0.10,
            "profit_factor": 1.5, "expectancy": 2.0, "total_trades": 8,
        },
        "mean_reversion_bbands": {
            "total_return": 0.25, "max_drawdown": 0.40,
            "profit_factor": 1.2, "expectancy": 1.0, "total_trades": 5,
        },
    }

    def fake_run(self, candles, *, strategy_key, timeframe, **kwargs):
        return {"ok": True, **metrics[strategy_key]}

    monkeypatch.setattr(BacktestEngine, "run_strategy", fake_run)
    result = StrategyBacktestSuite().compare(
        [], market_type="spot", timeframe="1h",
        strategy_keys=list(metrics),
    )
    assert result["ok"] is True
    # Return remains primary, so 25% ranks first; lower drawdown breaks the 20% tie.
    assert [row["strategy_key"] for row in result["leaderboard"]] == [
        "mean_reversion_bbands", "momentum_macd_rsi", "trend_following_ema",
    ]
    assert result["ranking_metric"] == (
        "total_return_then_lower_drawdown_then_profit_factor_then_expectancy"
    )


def test_strategy_leaderboard_handles_infinite_profit_factor_and_invalid_metrics(monkeypatch):
    def fake_run(self, candles, *, strategy_key, timeframe, **kwargs):
        if strategy_key == "trend_following_ema":
            return {
                "ok": True, "total_return": 0.1, "max_drawdown": 0.05,
                "profit_factor": "infinite", "expectancy": 1.0, "total_trades": 2,
            }
        return {
            "ok": True, "total_return": "nan", "max_drawdown": float("nan"),
            "profit_factor": None, "expectancy": None, "total_trades": 0,
        }

    monkeypatch.setattr(BacktestEngine, "run_strategy", fake_run)
    result = StrategyBacktestSuite().compare(
        [], market_type="spot", timeframe="1h",
        strategy_keys=["trend_following_ema", "momentum_macd_rsi"],
    )
    assert result["leaderboard"][0]["strategy_key"] == "trend_following_ema"
    assert result["leaderboard"][1]["strategy_key"] == "momentum_macd_rsi"
