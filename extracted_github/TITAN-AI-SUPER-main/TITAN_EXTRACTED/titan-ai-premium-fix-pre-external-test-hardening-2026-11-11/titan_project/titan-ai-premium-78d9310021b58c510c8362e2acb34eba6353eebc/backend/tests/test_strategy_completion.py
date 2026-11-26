from types import SimpleNamespace

from app.core.enums import MarketType, Timeframe
from app.engines.market_data import Candles
from app.engines.strategies import evaluate_strategy_candidates
from app.engines.technical import TechnicalAnalysisEngine


def _tf(trend, signals):
    return SimpleNamespace(trend=trend, signals=list(signals))


def test_technical_engine_exposes_requested_indicator_family_when_history_is_sufficient():
    rows = []
    for i in range(240):
        close = 100 + i * 0.15 + (i % 7) * 0.08
        rows.append({
            "open": close - 0.05, "high": close + 0.4,
            "low": close - 0.4, "close": close, "volume": 100 + i,
        })
    result = TechnicalAnalysisEngine().analyze(Candles(rows), Timeframe.H1)
    for key in (
        "ema9", "ema21", "ema50", "ema200", "sma20", "sma50", "vwap",
        "supertrend", "supertrend_direction", "ichimoku_tenkan",
        "ichimoku_kijun", "ichimoku_span_a", "ichimoku_span_b",
        "stoch_rsi_k", "stoch_rsi_d", "cci20",
    ):
        assert result.indicators[key] is not None, key


def test_multi_strategy_engine_ranks_all_directional_candidates_and_selects_best():
    analyses = {
        "1d": _tf("bullish", ["price_above_ema20_ema50"]),
        "4h": _tf("bullish", [
            "ema9_above_ema21", "price_above_ema200", "supertrend_bullish",
            "price_above_ichimoku_cloud", "macd_hist_positive",
        ]),
        "1h": _tf("bullish", [
            "ema9_above_ema21", "price_above_ema200", "supertrend_bullish",
            "price_above_ichimoku_cloud", "macd_hist_positive",
        ]),
        "15m": _tf("bullish", ["macd_hist_positive"]),
        "5m": _tf("bullish", ["macd_hist_positive", "volume_spike"]),
    }
    result = evaluate_strategy_candidates(MarketType.SPOT, "bullish", analyses)
    assert result["status"] == "selected"
    assert result["selected"]["key"] == "trend_following_ema"
    keys = {item["key"] for item in result["candidates"]}
    assert {"trend_following_ema", "momentum_macd_rsi", "scalping_m5",
            "swing_h1_h4", "mean_reversion_bbands", "breakout_structure",
            "liquidity_orderflow"} <= keys


def test_multi_strategy_engine_fails_closed_when_qualified_directions_conflict():
    analyses = {
        "1d": _tf("bullish", []),
        "4h": _tf("bullish", [
            "ema9_above_ema21", "price_above_ema200", "supertrend_bullish",
            "price_above_ichimoku_cloud", "macd_hist_positive",
        ]),
        "1h": _tf("bullish", [
            "ema9_above_ema21", "price_above_ema200", "supertrend_bullish",
            "price_above_ichimoku_cloud", "macd_hist_positive",
        ]),
        "15m": _tf("bearish", [
            "bollinger_upper_band_touch", "rsi_overbought", "cci_overbought",
        ]),
        "5m": _tf("bullish", []),
    }
    result = evaluate_strategy_candidates(MarketType.SPOT, "bullish", analyses)
    assert result["status"] == "conflicting_qualified_signals"
    assert result["selected"] is None


def test_strategy_evaluator_does_not_activate_trade_candidates_for_earn():
    result = evaluate_strategy_candidates(
        MarketType.EARN, "bullish", {"1h": _tf("bullish", ["macd_hist_positive"])}
    )
    assert result["selected"] is None
    assert result["candidates"] == []


def test_candidate_ranker_does_not_count_bearish_indicators_for_bullish_trend():
    analyses = {
        "1d": _tf("bullish", []),
        "4h": _tf("bullish", [
            "ema9_below_ema21", "price_below_ema200", "supertrend_bearish",
            "price_below_ichimoku_cloud", "macd_hist_negative",
        ]),
        "1h": _tf("bullish", [
            "ema9_below_ema21", "price_below_ema200", "supertrend_bearish",
            "price_below_ichimoku_cloud", "macd_hist_negative",
        ]),
        "15m": _tf("bullish", []),
        "5m": _tf("bullish", []),
    }
    result = evaluate_strategy_candidates(MarketType.SPOT, "bullish", analyses)
    trend = next(x for x in result["candidates"] if x["key"] == "trend_following_ema")
    assert trend["score"] == 0
    assert trend["qualified"] is False


def test_candidate_ranker_rejects_mixed_direction_structural_evidence():
    analyses = {
        "1d": _tf("bullish", []),
        "4h": _tf("bullish", []),
        "1h": _tf("bullish", []),
        "15m": _tf("bullish", ["breakout_bullish", "bos_bearish", "volume_spike"]),
        "5m": _tf("bullish", []),
    }
    result = evaluate_strategy_candidates(MarketType.SPOT, "bullish", analyses)
    breakout = next(x for x in result["candidates"] if x["key"] == "breakout_structure")
    assert breakout["qualified"] is False
    assert breakout["direction"] == "neutral"


def test_mean_reversion_requires_band_rsi_and_secondary_oscillator_same_direction():
    incomplete = {
        "1h": _tf("bullish", ["bollinger_lower_band_touch", "rsi_oversold"]),
        "15m": _tf("bullish", ["cci_overbought"]),
    }
    result = evaluate_strategy_candidates(MarketType.SPOT, "bullish", incomplete)
    mean_reversion = next(x for x in result["candidates"] if x["key"] == "mean_reversion_bbands")
    assert mean_reversion["qualified"] is False
    assert mean_reversion["direction"] == "neutral"


def test_orderflow_candidate_must_match_the_decision_symbol():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    micro = {
        "liquidity": {
            "ok": True, "source": "binance_public_read_only", "symbol": "BTCUSDT",
            "fetched_at": (now - timedelta(seconds=1)).isoformat(),
            "spread_percent": 0.01, "depth_imbalance": 0.35,
        },
        "order_flow": {
            "ok": True, "source": "binance_public_read_only", "symbol": "BTCUSDT",
            "first_trade_time": (now - timedelta(seconds=10)).isoformat(),
            "last_trade_time": (now - timedelta(seconds=1)).isoformat(),
            "taker_flow_imbalance": 0.30,
        },
    }
    wrong_symbol = evaluate_strategy_candidates(
        MarketType.SPOT, "bullish", {}, micro, expected_symbol="ETHUSDT",
    )
    candidate = next(x for x in wrong_symbol["candidates"] if x["key"] == "liquidity_orderflow")
    assert candidate["qualified"] is False
    assert candidate["score"] == 0

    matching_symbol = evaluate_strategy_candidates(
        MarketType.SPOT, "bullish", {}, micro, expected_symbol="BTCUSDT",
    )
    candidate = next(x for x in matching_symbol["candidates"] if x["key"] == "liquidity_orderflow")
    assert candidate["qualified"] is True


def test_orderflow_candidate_rejects_incomplete_window_and_out_of_range_imbalance():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    base = {
        "liquidity": {
            "ok": True, "source": "binance_public_read_only", "symbol": "BTCUSDT",
            "fetched_at": (now - timedelta(seconds=1)).isoformat(),
            "spread_percent": 0.01, "depth_imbalance": 0.35,
        },
        "order_flow": {
            "ok": True, "source": "binance_public_read_only", "symbol": "BTCUSDT",
            "first_trade_time": (now - timedelta(seconds=10)).isoformat(),
            "last_trade_time": (now - timedelta(seconds=1)).isoformat(),
            "taker_flow_imbalance": 0.30,
        },
    }
    missing_window = {
        "liquidity": dict(base["liquidity"]),
        "order_flow": {key: value for key, value in base["order_flow"].items()
                       if key != "first_trade_time"},
    }
    invalid_imbalance = {
        "liquidity": {**base["liquidity"], "depth_imbalance": 1.5},
        "order_flow": dict(base["order_flow"]),
    }
    for evidence in (missing_window, invalid_imbalance):
        result = evaluate_strategy_candidates(
            MarketType.SPOT, "bullish", {}, evidence, expected_symbol="BTCUSDT",
        )
        candidate = next(x for x in result["candidates"] if x["key"] == "liquidity_orderflow")
        assert candidate["qualified"] is False
        assert candidate["score"] == 0


def test_orderflow_candidate_rejects_boolean_imbalance_metrics():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    micro = {
        "liquidity": {
            "ok": True, "source": "binance_public_read_only", "symbol": "BTCUSDT",
            "fetched_at": (now - timedelta(seconds=1)).isoformat(),
            "spread_percent": 0.01, "depth_imbalance": True,
        },
        "order_flow": {
            "ok": True, "source": "binance_public_read_only", "symbol": "BTCUSDT",
            "first_trade_time": (now - timedelta(seconds=10)).isoformat(),
            "last_trade_time": (now - timedelta(seconds=1)).isoformat(),
            "taker_flow_imbalance": 0.30,
        },
    }
    result = evaluate_strategy_candidates(
        MarketType.SPOT, "bullish", {}, micro, expected_symbol="BTCUSDT",
    )
    candidate = next(x for x in result["candidates"] if x["key"] == "liquidity_orderflow")
    assert candidate["qualified"] is False
    assert candidate["score"] == 0


def test_strategy_registry_status_matches_executable_historical_coverage():
    from app.engines.strategies import list_strategies

    registry = {item["key"]: item for item in list_strategies()}
    completed_directional = {
        "trend_following_ema", "momentum_macd_rsi", "mean_reversion_bbands",
        "breakout_structure", "scalping_m5", "swing_h1_h4",
    }
    assert all(registry[key]["status"] == "implemented" for key in completed_directional)
    assert registry["liquidity_orderflow"]["status"] == "implemented_partial"
    assert registry["arbitrage"]["status"] == "implemented_partial"


def test_breakout_strategy_requires_sweep_fvg_and_close_strength_confluence():
    from app.engines.backtest import BacktestEngine
    analysis=SimpleNamespace(
        trend="bullish",signals=[],indicators={},
        structure={
            "breakouts":[],
            "liquidity_sweeps":[{"type":"sell_side_liquidity_sweep","direction":"bullish"}],
            "fair_value_gaps":[{"direction":"bullish","status":"open"}],
            "bos_choch":[],
            "market_regime":{"trend":"ranging"},
        },
        candles={"patterns":[],"close_strength":0.8},
        timeframe=SimpleNamespace(value="15m"),
    )
    assert BacktestEngine._strategy_action("breakout_structure",analysis)=="buy"


def test_breakout_smc_countertrend_requires_same_direction_choch():
    from app.engines.backtest import BacktestEngine
    analysis=SimpleNamespace(
        trend="bullish",signals=[],indicators={},
        structure={
            "breakouts":[],
            "liquidity_sweeps":[{"direction":"bullish"}],
            "fair_value_gaps":[{"direction":"bullish","status":"open"}],
            "bos_choch":[],
            "market_regime":{"trend":"trending_bearish"},
        },
        candles={"patterns":[],"close_strength":0.8},
        timeframe=SimpleNamespace(value="15m"),
    )
    assert BacktestEngine._strategy_action("breakout_structure",analysis) is None
    analysis.structure["bos_choch"]=[{"type":"CHOCH","direction":"bullish"}]
    assert BacktestEngine._strategy_action("breakout_structure",analysis)=="buy"


def test_futures_strategy_context_uses_fresh_public_funding_data(monkeypatch):
    from datetime import datetime, timezone
    from app.data import binance_futures_public
    from app.core.enums import MarketType
    from app.services.core_service import TitanCoreService

    now_ms = datetime.now(timezone.utc).timestamp() * 1000.0
    monkeypatch.setattr(
        binance_futures_public.BinanceFuturesPublicData,
        "get_mark_price",
        lambda self, symbol: {
            "symbol": "BTCUSDT", "last_funding_rate": 0.0002,
            "time": now_ms, "source": "binance_usdm_futures_public_read_only",
        },
    )
    context = TitanCoreService._build_strategy_context("BTC/USDT", MarketType.FUTURES, "binance_public")
    funding = context["funding_rate"]
    assert funding["ok"] is True
    assert funding["source"] == "binance_futures_public_read_only"
    assert funding["symbol"] == "BTCUSDT"
    assert funding["funding_rate"] == 0.0002
    assert datetime.fromisoformat(funding["fetched_at"]).tzinfo is not None


def test_futures_strategy_context_fails_closed_for_spot_or_synthetic_data(monkeypatch):
    from app.data import binance_futures_public
    from app.core.enums import MarketType
    from app.services.core_service import TitanCoreService

    def unexpected_call(*args, **kwargs):
        raise AssertionError("public Futures adapter must not be called")
    monkeypatch.setattr(binance_futures_public.BinanceFuturesPublicData, "get_mark_price", unexpected_call)
    assert TitanCoreService._build_strategy_context("BTC/USDT", MarketType.SPOT, "binance_public") == {}
    assert TitanCoreService._build_strategy_context("BTC/USDT", MarketType.FUTURES, "synthetic") == {}


def test_futures_strategy_context_rejects_stale_funding_snapshot(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.data import binance_futures_public
    from app.core.enums import MarketType
    from app.services.core_service import TitanCoreService

    stale_ms = (datetime.now(timezone.utc) - timedelta(minutes=3)).timestamp() * 1000.0
    monkeypatch.setattr(
        binance_futures_public.BinanceFuturesPublicData,
        "get_mark_price",
        lambda self, symbol: {
            "symbol": "BTCUSDT", "last_funding_rate": 0.0002,
            "time": stale_ms, "source": "binance_usdm_futures_public_read_only",
        },
    )
    assert TitanCoreService._build_strategy_context("BTC/USDT", MarketType.FUTURES, "binance_public") == {}


def test_fresh_futures_funding_context_qualifies_contrarian_strategy():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    context = {
        "funding_rate": {
            "ok": True,
            "source": "binance_futures_public_read_only",
            "symbol": "BTCUSDT",
            "fetched_at": now.isoformat(),
            "funding_rate": 0.0002,
        }
    }
    result = evaluate_strategy_candidates(
        MarketType.FUTURES, "bearish", {}, strategy_context=context,
        expected_symbol="BTCUSDT",
    )
    funding = next(item for item in result["candidates"] if item["key"] == "funding_rate")
    assert funding["qualified"] is True
    assert funding["direction"] == "bearish"
    assert "fresh_funding_rate" in funding["evidence"]
