from __future__ import annotations

from datetime import datetime, timezone
import math

from app.core.enums import MarketType
from .advanced_strategies import ADVANCED_STRATEGIES, evaluate_advanced_candidates


# Extensible strategy registry (examples + metadata). Not a claim of completeness or profitability.
STRATEGIES = [
    {"key": "trend_following_ema", "name": "Trend Following (EMA)", "category": "trend", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "momentum_macd_rsi", "name": "Momentum (MACD/RSI)", "category": "momentum", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "mean_reversion_bbands", "name": "Mean Reversion (Bollinger Bands)", "category": "mean_reversion", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "breakout_structure", "name": "Breakout / Market Structure", "category": "breakout", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "scalping_m5", "name": "Scalping (M5 timing)", "category": "scalping", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "swing_h1_h4", "name": "Swing (H1/H4)", "category": "swing", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "liquidity_orderflow", "name": "Liquidity/Order Flow", "category": "order_flow", "markets": ["spot", "futures"], "status": "implemented_partial"},
    {"key": "arbitrage", "name": "Triangular Arbitrage (Spot)", "category": "arbitrage", "markets": ["spot"], "status": "implemented_partial"},
    {"key": "adx_trend", "name": "ADX Trend Strength", "category": "trend", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "ichimoku_cloud", "name": "Ichimoku Cloud", "category": "trend", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "parabolic_sar", "name": "Parabolic SAR", "category": "trend", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "multi_timeframe_trend", "name": "Multi-Timeframe Trend", "category": "trend", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "order_block", "name": "Order Block", "category": "smc", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "fair_value_gap", "name": "Fair Value Gap", "category": "smc", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "liquidity_sweep", "name": "Liquidity Sweep", "category": "smc", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "breaker_block", "name": "Breaker Block", "category": "smc", "markets": ["spot", "futures"], "status": "implemented_partial"},
    {"key": "premium_discount", "name": "Premium & Discount", "category": "smc", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "volume_profile", "name": "Volume Profile", "category": "volume", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "vwap_reversion", "name": "VWAP Reversion", "category": "mean_reversion", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "funding_rate", "name": "Funding Rate", "category": "derivatives", "markets": ["futures"], "status": "implemented_partial"},
    {"key": "open_interest_divergence", "name": "Open Interest Divergence", "category": "derivatives", "markets": ["futures"], "status": "implemented_partial"},
    {"key": "statistical_arbitrage", "name": "Statistical Arbitrage", "category": "arbitrage", "markets": ["spot", "futures"], "status": "implemented_partial"},
    {"key": "pairs_trading", "name": "Pairs Trading", "category": "arbitrage", "markets": ["spot", "futures"], "status": "implemented_partial"},
    {"key": "opening_range_breakout", "name": "Opening Range Breakout", "category": "breakout", "markets": ["spot", "futures"], "status": "implemented_partial"},
    {"key": "vwap_scalping", "name": "VWAP Scalping", "category": "scalping", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "order_book_imbalance", "name": "Order Book Imbalance", "category": "order_flow", "markets": ["spot", "futures"], "status": "implemented_partial"},
    {"key": "cumulative_volume_delta", "name": "Cumulative Volume Delta", "category": "order_flow", "markets": ["spot", "futures"], "status": "implemented_partial"},
    {"key": "momentum_burst", "name": "Momentum Burst", "category": "momentum", "markets": ["spot", "futures"], "status": "implemented"},
    {"key": "ict_smc_model", "name": "ICT Trading Model", "category": "ict_smc", "markets": ["spot", "futures"], "status": "implemented_partial"},
]


def list_strategies() -> list[dict]:
    return list(STRATEGIES)


def select_strategy(
    market: MarketType,
    bias: str,
    signals: list[str],
    timeframe_analyses: dict | None = None,
    microstructure_analysis: dict | None = None,
    expected_symbol: str | None = None,
) -> dict:
    """Select only a strategy with matching implemented evidence; never guess a fallback."""
    active = [
        strategy for strategy in STRATEGIES
        if market.value in strategy["markets"]
        and strategy["status"] in {"implemented", "implemented_partial"}
    ]
    signal_set = set(signals or [])
    by_key = {strategy["key"]: strategy for strategy in active}
    tf = {
        (key.value if hasattr(key, "value") else str(key)): value
        for key, value in (timeframe_analyses or {}).items()
    }
    d1, h4, h1, m15, m5 = (tf.get(name) for name in ("1d", "4h", "1h", "15m", "5m"))


    # M5 entries require a matching 15M setup and higher-timeframe bias.
    if "scalping_m5" in by_key and bias in {"bullish", "bearish"} and m5 and m15 and m5.trend == bias and m15.trend == bias:
        m5_signals = set(m5.signals or [])
        direction_signal = "macd_hist_positive" if bias == "bullish" else "macd_hist_negative"
        opposite_rsi = "rsi_overbought" if bias == "bullish" else "rsi_oversold"
        candle_confirm = (
            {"bullish_engulfing", "morning_star", "pin_bar_bullish", "hammer"}
            if bias == "bullish"
            else {"bearish_engulfing", "evening_star", "pin_bar_bearish", "shooting_star"}
        )
        if direction_signal in m5_signals and opposite_rsi not in m5_signals and (
            "volume_spike" in m5_signals or bool(candle_confirm.intersection(m5_signals))
        ):
            return by_key["scalping_m5"]

    # Public order-book depth and taker flow must agree with each other and
    # the higher-timeframe bias before the microstructure strategy is selectable.
    if "liquidity_orderflow" in by_key and isinstance(microstructure_analysis, dict):
        liquidity = microstructure_analysis.get("liquidity") or {}
        order_flow = microstructure_analysis.get("order_flow") or {}
        if not isinstance(liquidity, dict):
            liquidity = {}
        if not isinstance(order_flow, dict):
            order_flow = {}
        expected_source = (
            "binance_futures_public_read_only" if market == MarketType.FUTURES
            else "binance_public_read_only"
        )
        normalize_symbol = lambda value: "".join(
            character for character in str(value or "").upper() if character.isalnum()
        )
        liquidity_symbol = normalize_symbol(liquidity.get("symbol"))
        flow_symbol = normalize_symbol(order_flow.get("symbol"))
        requested_symbol = normalize_symbol(expected_symbol) if expected_symbol else None
        symbols_match = bool(liquidity_symbol) and liquidity_symbol == flow_symbol
        if requested_symbol is not None:
            symbols_match = symbols_match and liquidity_symbol == requested_symbol
        if (liquidity.get("ok") is True and order_flow.get("ok") is True
                and liquidity.get("source") == expected_source
                and order_flow.get("source") == expected_source
                and symbols_match):
            try:
                # Never let an old REST snapshot authorize a current strategy.
                # The order-book and aggregate-trade windows must both be fresh
                # and temporally coherent; missing timestamps fail closed.
                now = datetime.now(timezone.utc)
                book_time = datetime.fromisoformat(
                    str(liquidity["fetched_at"]).replace("Z", "+00:00")
                )
                first_trade_time = datetime.fromisoformat(
                    str(order_flow["first_trade_time"]).replace("Z", "+00:00")
                )
                last_trade_time = datetime.fromisoformat(
                    str(order_flow["last_trade_time"]).replace("Z", "+00:00")
                )
                if any(stamp.tzinfo is None for stamp in (book_time, first_trade_time, last_trade_time)):
                    raise ValueError("microstructure_timestamps_must_be_timezone_aware")
                ages = [(now - stamp).total_seconds() for stamp in (book_time, last_trade_time)]
                if (any(age < -5 or age > 120 for age in ages)
                        or first_trade_time > last_trade_time
                        or (last_trade_time - first_trade_time).total_seconds() > 120
                        or abs((book_time - last_trade_time).total_seconds()) > 120):
                    raise ValueError("stale_or_incoherent_microstructure_evidence")
                raw_metrics = (
                    liquidity["spread_percent"], liquidity["depth_imbalance"],
                    order_flow["taker_flow_imbalance"],
                )
                if any(isinstance(value, bool) for value in raw_metrics):
                    raise ValueError("boolean_microstructure_metric")
                spread, depth_imbalance, flow_imbalance = (
                    float(value) for value in raw_metrics
                )
                metrics_valid = all(
                    math.isfinite(value) for value in (spread, depth_imbalance, flow_imbalance)
                )
            except (KeyError, TypeError, ValueError, OverflowError):
                metrics_valid = False
            if (metrics_valid and 0 <= spread <= 0.20
                    and -1.0 <= depth_imbalance <= 1.0
                    and -1.0 <= flow_imbalance <= 1.0):
                if (bias == "bullish" and depth_imbalance >= 0.15 and flow_imbalance >= 0.15):
                    return by_key["liquidity_orderflow"]
                if (bias == "bearish" and depth_imbalance <= -0.15 and flow_imbalance <= -0.15):
                    return by_key["liquidity_orderflow"]

    # Swing entries need H1/H4 agreement and no opposing daily trend.
    if ("swing_h1_h4" in by_key and bias in {"bullish", "bearish"} and h4 and h1
            and h4.trend == bias and h1.trend == bias
            and (m15 is None or m15.trend == bias)):
        daily_ok = d1 is None or d1.trend in {bias, "sideways"}
        direction_signal = "macd_hist_positive" if bias == "bullish" else "macd_hist_negative"
        if daily_ok and direction_signal in set(h4.signals or []) and direction_signal in set(h1.signals or []):
            return by_key["swing_h1_h4"]

    structural_source = (m15, m5) if timeframe_analyses is not None else ()
    structural_pool = (
        [signal for analysis in structural_source if analysis is not None for signal in (analysis.signals or [])]
        if timeframe_analyses is not None else list(signal_set)
    )
    structural_signals = [
        str(signal).lower() for signal in structural_pool
        if "breakout" in str(signal).lower()
        or str(signal).lower().startswith(("bos_", "choch_"))
    ]
    if structural_signals:
        direction_matches = bias in {"bullish", "bearish"} and all(
            signal.endswith(bias) or signal == "breakout"
            for signal in structural_signals
        )
        if direction_matches:
            return by_key.get("breakout_structure", {
                "key": "none", "name": "No active strategy", "category": "none",
                "markets": [market.value], "status": "no_matching_implementation",
            })
        return {
            "key": "none", "name": "No strategy selected", "category": "none",
            "markets": [market.value], "status": "structural_signal_conflicts_with_higher_timeframe_bias",
        }
    if (
        ("rsi_oversold" in signal_set and "bollinger_lower_band_touch" in signal_set)
        or ("rsi_overbought" in signal_set and "bollinger_upper_band_touch" in signal_set)
    ):
        return by_key.get("mean_reversion_bbands", {
            "key": "none", "name": "No active strategy", "category": "none",
            "markets": [market.value], "status": "no_matching_implementation",
        })
    if bias in {"bullish", "bearish"} and (
        "price_above_ema20_ema50" in signal_set
        or "price_below_ema20_ema50" in signal_set
    ):
        return by_key.get("trend_following_ema", {
            "key": "none", "name": "No active strategy", "category": "none",
            "markets": [market.value], "status": "no_matching_implementation",
        })
    if "macd_hist_positive" in signal_set or "macd_hist_negative" in signal_set:
        return by_key.get("momentum_macd_rsi", {
            "key": "none", "name": "No active strategy", "category": "none",
            "markets": [market.value], "status": "no_matching_implementation",
        })
    return {
        "key": "none",
        "name": "No strategy selected",
        "category": "none",
        "markets": [market.value],
        "status": "no_confirmed_strategy_signal",
    }


def evaluate_strategy_candidates(
    market: MarketType,
    bias: str,
    timeframe_analyses: dict | None = None,
    microstructure_analysis: dict | None = None,
    expected_symbol: str | None = None,
    strategy_context: dict | None = None,
) -> dict:
    """Score every compatible strategy independently and fail closed on weak/conflicting evidence.

    Scores are evidence counts, not probabilities or expected returns. Arbitrage remains
    in its dedicated route simulator and is deliberately not mixed with directional entries.
    """
    tf = {
        (key.value if hasattr(key, "value") else str(key)): value
        for key, value in (timeframe_analyses or {}).items()
    }
    def signals(name):
        item = tf.get(name)
        return set(getattr(item, "signals", []) or []) if item else set()
    def trend(name):
        item = tf.get(name)
        return getattr(item, "trend", "unknown") if item else "unknown"
    candidates = []
    def add(key, score, reasons, direction):
        meta = next((x for x in STRATEGIES if x["key"] == key), None)
        if meta and market.value in meta["markets"]:
            candidates.append({"key": key, "name": meta["name"], "score": score,
                               "direction": direction, "evidence": reasons,
                               "qualified": score >= 3})
    # Trend following: stacked averages, long-term regime and trend filter.
    d1s, h4s, h1s = signals("1d"), signals("4h"), signals("1h")
    bullish = bias == "bullish"
    bearish = bias == "bearish"
    trend_evidence=[]
    directional_trend_signals = (
        ("ema9_above_ema21", "price_above_ema200", "supertrend_bullish",
         "price_above_ichimoku_cloud", "price_above_ema20_ema50", "macd_hist_positive")
        if bullish else
        ("ema9_below_ema21", "price_below_ema200", "supertrend_bearish",
         "price_below_ichimoku_cloud", "price_below_ema20_ema50", "macd_hist_negative")
        if bearish else ()
    )
    for name in ("4h","1h"):
        ss=signals(name)
        trend_evidence += [f"{name}:{x}" for x in directional_trend_signals if x in ss]
    opposing_trend_signals = (
        {"ema9_below_ema21", "price_below_ema200", "supertrend_bearish",
         "price_below_ichimoku_cloud", "price_below_ema20_ema50", "macd_hist_negative"}
        if bullish else
        {"ema9_above_ema21", "price_above_ema200", "supertrend_bullish",
         "price_above_ichimoku_cloud", "price_above_ema20_ema50", "macd_hist_positive"}
        if bearish else set()
    )
    has_opposing_trend_evidence = any(
        bool(signals(name).intersection(opposing_trend_signals)) for name in ("4h", "1h")
    )
    if has_opposing_trend_evidence:
        trend_evidence = []
    elif bullish and trend("1d") == trend("4h") == trend("1h") == "bullish":
        trend_evidence.append("higher_timeframe_consensus")
    elif bearish and trend("1d") == trend("4h") == trend("1h") == "bearish":
        trend_evidence.append("higher_timeframe_consensus")
    trend_score=len(set(trend_evidence))
    add("trend_following_ema",trend_score,sorted(set(trend_evidence)),bias)
    # Momentum evidence must point in the selected direction; opposing MACD/oscillator
    # signals cannot inflate a candidate's score.
    mom=[]
    macd_signal = "macd_hist_positive" if bullish else "macd_hist_negative" if bearish else None
    oscillator_signal = (
        ("stoch_rsi_oversold", "rsi_oversold") if bullish
        else ("stoch_rsi_overbought", "rsi_overbought") if bearish else ()
    )
    for name in ("1h","15m"):
        ss=signals(name)
        if macd_signal and macd_signal in ss:
            mom.append(macd_signal)
        mom += [x for x in oscillator_signal if x in ss]
    add("momentum_macd_rsi",len(set(mom)),sorted(set(mom)),bias)
    # Mean reversion gets its own counter-trend direction, but only if band,
    # RSI and a second oscillator all confirm the same side.
    mr_bull=[]; mr_bear=[]
    for name in ("1h","15m"):
        ss=signals(name)
        mr_bull += [f"{name}:{x}" for x in (
            "bollinger_lower_band_touch","rsi_oversold","cci_oversold","stoch_rsi_oversold"
        ) if x in ss]
        mr_bear += [f"{name}:{x}" for x in (
            "bollinger_upper_band_touch","rsi_overbought","cci_overbought","stoch_rsi_overbought"
        ) if x in ss]
    bull_mr = set(mr_bull); bear_mr = set(mr_bear)
    bull_complete = (any(x.endswith(":bollinger_lower_band_touch") for x in bull_mr)
                     and any(x.endswith(":rsi_oversold") for x in bull_mr)
                     and any(x.endswith((":cci_oversold", ":stoch_rsi_oversold")) for x in bull_mr))
    bear_complete = (any(x.endswith(":bollinger_upper_band_touch") for x in bear_mr)
                     and any(x.endswith(":rsi_overbought") for x in bear_mr)
                     and any(x.endswith((":cci_overbought", ":stoch_rsi_overbought")) for x in bear_mr))
    mr_direction = "bullish" if bull_complete and not bear_complete else "bearish" if bear_complete and not bull_complete else "neutral"
    mr_evidence = sorted(bull_mr if mr_direction == "bullish" else bear_mr if mr_direction == "bearish" else set())
    add("mean_reversion_bbands",len(mr_evidence) if mr_direction != "neutral" else 0,mr_evidence,mr_direction)
    # Confirmed structural events must all agree with the higher-timeframe bias.
    structural=[]
    for name in ("15m","5m"):
        structural += [x for x in signals(name) if x.startswith(("bos_","choch_","breakout_"))]
    structural_directions = {
        "bullish" if x.endswith("bullish") else "bearish" if x.endswith("bearish") else "unknown"
        for x in structural
    }
    vol_confirm=any("volume_spike" in signals(name) for name in ("15m","5m"))
    structural_ok = bool(structural) and len(structural_directions) == 1 and bias in structural_directions
    structural_evidence = sorted(set(structural)) if structural_ok else []
    if structural_ok and vol_confirm:
        structural_evidence.append("volume_spike")
    add("breakout_structure",len(structural_evidence),structural_evidence,bias if structural_ok else "neutral")
    # M5 scalping only counts direction-matched triggers and candle patterns.
    scalp=signals("5m")
    scalp_direction=trend("5m")
    scalp_evidence=[]
    if scalp_direction in {"bullish","bearish"} and scalp_direction == trend("15m") and scalp_direction == bias:
        scalp_evidence.append("m5_m15_trend_alignment")
        expected = (
            ("macd_hist_positive","bullish_engulfing","pin_bar_bullish","hammer")
            if scalp_direction == "bullish" else
            ("macd_hist_negative","bearish_engulfing","pin_bar_bearish","shooting_star")
        )
        scalp_evidence += [x for x in expected if x in scalp]
        if "volume_spike" in scalp:
            scalp_evidence.append("volume_spike")
        if trend("1h") in {scalp_direction,"sideways"}:
            scalp_evidence.append("h1_context_aligned")
    add("scalping_m5",len(set(scalp_evidence)),sorted(set(scalp_evidence)),scalp_direction)
    # Swing uses direction-matched H4/H1 agreement and daily context.
    swing=[]
    swing_direction=trend("4h")
    if swing_direction in {"bullish","bearish"} and swing_direction == trend("1h") == bias:
        swing.append("h4_h1_alignment")
        if trend("1d") in {swing_direction,"sideways"}:
            swing.append("daily_context_aligned")
        expected = ("macd_hist_positive","supertrend_bullish") if swing_direction == "bullish" else ("macd_hist_negative","supertrend_bearish")
        swing += [x for x in expected if x in h4s.intersection(h1s)]
    add("swing_h1_h4",len(set(swing)),sorted(set(swing)),swing_direction)
    # Order-flow strategy is eligible only with fresh, same-symbol, coherent read-only evidence.
    flow_score=0; flow_evidence=[]
    micro=microstructure_analysis if isinstance(microstructure_analysis,dict) else {}
    liq=micro.get("liquidity") or {}; flow=micro.get("order_flow") or {}
    if isinstance(liq,dict) and isinstance(flow,dict):
        expected="binance_futures_public_read_only" if market==MarketType.FUTURES else "binance_public_read_only"
        try:
            bt=datetime.fromisoformat(str(liq["fetched_at"]).replace("Z","+00:00"))
            first_trade_time=datetime.fromisoformat(str(flow["first_trade_time"]).replace("Z","+00:00"))
            ft=datetime.fromisoformat(str(flow["last_trade_time"]).replace("Z","+00:00"))
            age=(datetime.now(timezone.utc)-bt).total_seconds()
            flow_age=(datetime.now(timezone.utc)-ft).total_seconds()
            normalize_symbol = lambda value: "".join(
                character for character in str(value or "").upper() if character.isalnum()
            )
            liquidity_symbol = normalize_symbol(liq["symbol"])
            flow_symbol = normalize_symbol(flow["symbol"])
            requested_symbol = normalize_symbol(expected_symbol) if expected_symbol else None
            same = bool(liquidity_symbol) and liquidity_symbol == flow_symbol
            if requested_symbol is not None:
                same = same and liquidity_symbol == requested_symbol
            if (liq.get("ok") is True and flow.get("ok") is True and liq.get("source")==expected
                and flow.get("source")==expected and same
                and bt.tzinfo and first_trade_time.tzinfo and ft.tzinfo
                and -5 <= age <= 120 and -5 <= flow_age <= 120
                and first_trade_time <= ft
                and (ft-first_trade_time).total_seconds() <= 120
                and abs((bt-ft).total_seconds())<=120):
                raw_metrics = (liq["depth_imbalance"], flow["taker_flow_imbalance"], liq["spread_percent"])
                if any(isinstance(value, bool) for value in raw_metrics):
                    raise ValueError("boolean_orderflow_metric")
                di=float(raw_metrics[0]); fi=float(raw_metrics[1])
                spread=float(raw_metrics[2])
                if (all(math.isfinite(v) for v in (di,fi,spread))
                        and -1.0 <= di <= 1.0 and -1.0 <= fi <= 1.0
                        and 0 <= spread <= 0.20):
                    if bias=="bullish" and di>=0.15 and fi>=0.15: flow_score=3; flow_evidence=["fresh_depth_buy_imbalance","fresh_taker_buy_imbalance","spread_acceptable"]
                    elif bias=="bearish" and di<=-0.15 and fi<=-0.15: flow_score=3; flow_evidence=["fresh_depth_sell_imbalance","fresh_taker_sell_imbalance","spread_acceptable"]
        except (KeyError,TypeError,ValueError,OverflowError):
            pass
    add("liquidity_orderflow",flow_score,flow_evidence,bias)
    candidates.extend(evaluate_advanced_candidates(
        market, bias, timeframe_analyses=tf, microstructure_analysis=microstructure_analysis,
        strategy_context=strategy_context, expected_symbol=expected_symbol,
    ))
    ranked=sorted(candidates,key=lambda x:(x["qualified"],x["score"],x["key"]),reverse=True)
    qualified=[x for x in ranked if x["qualified"] and x["direction"] in {"bullish","bearish"}]
    directions={x["direction"] for x in qualified}
    winner=qualified[0] if qualified and len(directions)==1 else None
    return {"selected":winner,"candidates":ranked,"qualified_count":len(qualified),
            "status":"selected" if winner else "conflicting_qualified_signals" if len(directions)>1 else "no_qualified_strategy",
            "score_note":"Evidence score, not probability, forecast, or profit guarantee."}
