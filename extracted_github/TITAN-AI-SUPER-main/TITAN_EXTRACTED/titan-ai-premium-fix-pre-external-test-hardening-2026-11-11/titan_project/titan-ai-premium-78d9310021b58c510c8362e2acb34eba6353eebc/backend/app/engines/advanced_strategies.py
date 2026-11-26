from __future__ import annotations

from datetime import datetime, timezone
import math

from app.core.enums import MarketType


ADVANCED_STRATEGIES = [
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


def _valid_public_context(item, market, symbol, now):
    if not isinstance(item, dict) or item.get("ok") is not True:
        return False
    expected = "binance_futures_public_read_only" if market == MarketType.FUTURES else "binance_public_read_only"
    if item.get("source") != expected:
        return False
    if symbol and str(item.get("symbol", "")).upper().replace("/", "") != str(symbol).upper().replace("/", ""):
        return False
    try:
        stamp_value = item.get("fetched_at") or item.get("last_trade_time") or item.get("timestamp")
        if not stamp_value:
            return False
        stamp = datetime.fromisoformat(str(stamp_value).replace("Z", "+00:00"))
        age = (now - stamp).total_seconds()
        return stamp.tzinfo is not None and 0 <= age <= 120
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def evaluate_advanced_candidates(market, bias, timeframe_analyses=None, microstructure_analysis=None,
                                 strategy_context=None, expected_symbol=None):
    tf = {(k.value if hasattr(k, "value") else str(k)): v for k, v in (timeframe_analyses or {}).items()}
    def sig(frame):
        item = tf.get(frame)
        return set(getattr(item, "signals", []) or []) if item else set()
    def trend(frame):
        item = tf.get(frame)
        return getattr(item, "trend", "unknown") if item else "unknown"
    def ind(frame):
        item = tf.get(frame)
        return getattr(item, "indicators", {}) or {} if item else {}
    def structure(frame):
        item = tf.get(frame)
        return getattr(item, "structure", {}) or {} if item else {}
    def add(key, direction, evidence, min_score=3):
        meta = next(x for x in ADVANCED_STRATEGIES if x["key"] == key)
        clean = sorted(set(str(x) for x in evidence if x))
        score = len(clean)
        return {"key": key, "name": meta["name"], "score": score, "direction": direction,
                "evidence": clean, "qualified": score >= min_score}
    out = []
    now = datetime.now(timezone.utc)
    # ADX and PSAR are calculated by TechnicalAnalysisEngine; absence never creates a signal.
    for key, frame in (("adx_trend", "4h"), ("parabolic_sar", "1h")):
        i = ind(frame); direction = trend(frame); evidence = []
        if key == "adx_trend":
            adx = i.get("adx14")
            di_plus, di_minus = i.get("plus_di14"), i.get("minus_di14")
            if isinstance(adx, (int, float)) and math.isfinite(adx) and adx >= 20:
                evidence.append("adx_above_20")
                if direction == "bullish" and isinstance(di_plus, (int, float)) and isinstance(di_minus, (int, float)) and di_plus > di_minus:
                    evidence += ["positive_di_dominance", "bullish_trend"]
                elif direction == "bearish" and isinstance(di_plus, (int, float)) and isinstance(di_minus, (int, float)) and di_minus > di_plus:
                    evidence += ["negative_di_dominance", "bearish_trend"]
        else:
            psar = i.get("parabolic_sar")
            close = i.get("close")
            psar_direction = i.get("parabolic_sar_direction")
            if (
                isinstance(psar, (int, float))
                and isinstance(close, (int, float))
                and psar_direction in {"bullish", "bearish"}
                and psar_direction == direction
                and ((direction == "bullish" and psar < close) or (direction == "bearish" and psar > close))
            ):
                evidence += ["psar_direction_match", "price_on_correct_side"]
                if abs(close - psar) / max(close, 1e-12) <= 0.03:
                    evidence.append("psar_risk_distance_acceptable")
        out.append(add(key, direction, evidence))
    # Ichimoku: cloud location plus conversion/base-line ordering and trend agreement.
    i = ind("4h"); direction = trend("4h"); ev = []
    if direction == "bullish" and "price_above_ichimoku_cloud" in sig("4h") and i.get("ichimoku_tenkan") and i.get("ichimoku_kijun") and i["ichimoku_tenkan"] > i["ichimoku_kijun"]:
        ev = ["price_above_cloud", "tenkan_above_kijun", "h4_bullish"]
    elif direction == "bearish" and "price_below_ichimoku_cloud" in sig("4h") and i.get("ichimoku_tenkan") and i.get("ichimoku_kijun") and i["ichimoku_tenkan"] < i["ichimoku_kijun"]:
        ev = ["price_below_cloud", "tenkan_below_kijun", "h4_bearish"]
    out.append(add("ichimoku_cloud", direction, ev))
    # Full 1D/4H/1H trend stack, with 15M/5M timing confirmation.
    direction = bias
    ev = ["1d_4h_1h_alignment"] if direction in {"bullish", "bearish"} and trend("1d") == trend("4h") == trend("1h") == direction else []
    if ev and trend("15m") == direction: ev.append("15m_confirmation")
    if ev and trend("5m") == direction: ev.append("5m_entry_alignment")
    out.append(add("multi_timeframe_trend", direction, ev))
    # Structure-based strategies use only the completed-candle evidence already produced by the SMC engine.
    for key, field, prefix in (
        ("order_block", "order_blocks", "order_block_"),
        ("fair_value_gap", "fair_value_gaps", "fvg_"),
        ("liquidity_sweep", "liquidity_sweeps", "liquidity_sweep_"),
    ):
        ev=[]; direction="neutral"
        for frame in ("15m", "5m"):
            st=structure(frame)
            rows=st.get(field, []) if isinstance(st, dict) else []
            for row in rows[-3:] if isinstance(rows, list) else []:
                d=row.get("direction") if isinstance(row, dict) else None
                if d in {"bullish", "bearish"} and d == bias:
                    if key == "order_block" and row.get("status") not in {"candidate", "confirmed"}: continue
                    if key == "fair_value_gap" and row.get("status") != "open": continue
                    ev.append(frame + ":" + prefix + d)
                    direction=d
        out.append(add(key, direction, ev))
    # Breaker block requires an explicit, confirmed breaker event from a validated structure provider.
    ctx = strategy_context if isinstance(strategy_context, dict) else {}
    breaker = ctx.get("breaker_block")
    valid_breaker = _valid_public_context(breaker, market, expected_symbol, now)
    out.append(add("breaker_block", breaker.get("direction", "neutral") if valid_breaker else "neutral",
                   ["confirmed_breaker_block", "source_validated", "fresh_snapshot"] if valid_breaker and breaker.get("direction") == bias else []))
    # Premium/discount uses the latest confirmed swing range; price location must be near an edge.
    ev=[]; direction="neutral"
    for frame in ("4h", "1h"):
        st=structure(frame); indv=ind(frame)
        highs=(st.get("major_swings", {}).get("highs", []) if isinstance(st,dict) else [])
        lows=(st.get("major_swings", {}).get("lows", []) if isinstance(st,dict) else [])
        close=indv.get("close")
        if highs and lows and isinstance(close,(int,float)):
            hi=max(float(x["price"]) for x in highs if isinstance(x,dict) and "price" in x)
            lo=min(float(x["price"]) for x in lows if isinstance(x,dict) and "price" in x)
            if hi>lo:
                location=(close-lo)/(hi-lo)
                if location <= 0.35 and bias=="bullish": ev.append(frame+":discount_zone"); direction="bullish"
                elif location >= 0.65 and bias=="bearish": ev.append(frame+":premium_zone"); direction="bearish"
    if len(ev)>=2: ev.append("multi_timeframe_range_confirmation")
    out.append(add("premium_discount", direction, ev))
    # Volume profile is based on candle-volume allocation and explicitly labelled as an estimate.
    i=ind("1h"); direction=bias; ev=[]
    poc,vah,val=(i.get("volume_profile_poc"),i.get("volume_profile_vah"),i.get("volume_profile_val"))
    close=i.get("close")
    if all(isinstance(x,(int,float)) and math.isfinite(x) for x in (poc,vah,val,close)):
        if bias=="bullish" and close>poc and close<=vah: ev=["price_above_poc","inside_value_area","profile_estimate"]
        elif bias=="bearish" and close<poc and close>=val: ev=["price_below_poc","inside_value_area","profile_estimate"]
    out.append(add("volume_profile", direction, ev))
    # VWAP reversion requires a statistically meaningful deviation and oscillator extreme.
    for key, frame in (("vwap_reversion","1h"),("vwap_scalping","5m")):
        i=ind(frame); close=i.get("close"); vwap=i.get("vwap"); ev=[]; direction="neutral"
        if isinstance(close,(int,float)) and isinstance(vwap,(int,float)) and vwap>0:
            deviation=(close-vwap)/vwap
            if key=="vwap_reversion":
                if deviation <= -0.005 and ({"rsi_oversold","stoch_rsi_oversold"} & sig(frame)):
                    direction="bullish"; ev=["price_below_vwap_by_0_5pct","oversold_oscillator","vwap_reversion_target"]
                elif deviation >= 0.005 and ({"rsi_overbought","stoch_rsi_overbought"} & sig(frame)):
                    direction="bearish"; ev=["price_above_vwap_by_0_5pct","overbought_oscillator","vwap_reversion_target"]
            else:
                direction=bias
                if direction=="bullish" and close>vwap and trend("15m")=="bullish": ev=["m5_above_vwap","m15_bullish_alignment","higher_bias_confirmed"]
                elif direction=="bearish" and close<vwap and trend("15m")=="bearish": ev=["m5_below_vwap","m15_bearish_alignment","higher_bias_confirmed"]
        out.append(add(key,direction,ev))
    # Futures and cross-asset strategies require a fresh, provenance-checked public context.
    for key, field, expected_direction in (
        ("funding_rate","funding_rate",None),
        ("open_interest_divergence","open_interest",None),
        ("statistical_arbitrage","statistical_arbitrage",None),
        ("pairs_trading","pairs_trading",None),
        ("opening_range_breakout","opening_range",None),
        ("cumulative_volume_delta","cumulative_volume_delta",None),
    ):
        item=ctx.get(field); valid=_valid_public_context(item,market,expected_symbol,now)
        direction=item.get("direction","neutral") if valid else "neutral"
        if key == "funding_rate" and valid:
            rate = item.get("funding_rate")
            if isinstance(rate, (int, float)) and math.isfinite(rate):
                direction = "bearish" if rate > 0 else "bullish" if rate < 0 else "neutral"
        if key == "cumulative_volume_delta" and valid:
            delta = item.get("cvd_change")
            if isinstance(delta, (int, float)) and math.isfinite(delta):
                direction = "bullish" if delta > 0 else "bearish" if delta < 0 else "neutral"
        ev=[]
        if valid and direction in {"bullish","bearish"} and (direction == bias or key == "statistical_arbitrage"):
            if key=="funding_rate" and isinstance(item.get("funding_rate"),(int,float)) and abs(item["funding_rate"])>=0.0001:
                ev=["fresh_funding_rate","crowded_side_identified","contrarian_bias_confirmed"]
            elif key=="open_interest_divergence" and isinstance(item.get("open_interest_change_pct"),(int,float)) and isinstance(item.get("price_change_pct"),(int,float)) and abs(item["open_interest_change_pct"])>=1 and item["open_interest_change_pct"]*item["price_change_pct"]<0:
                ev=["fresh_open_interest","price_oi_divergence","direction_confirmed"]
            elif key=="statistical_arbitrage" and isinstance(item.get("z_score"),(int,float)) and abs(item["z_score"])>=2 and item.get("model_valid") is True:
                ev=["validated_model","zscore_extreme","mean_reversion_direction"]
            elif key=="pairs_trading" and isinstance(item.get("z_score"),(int,float)) and abs(item["z_score"])>=2 and item.get("cointegration_valid") is True:
                ev=["cointegration_valid","spread_zscore_extreme","pair_hedge_direction"]
            elif key=="opening_range_breakout" and item.get("range_confirmed") is True and item.get("volume_confirmed") is True and item.get("close_confirmed") is True:
                ev=["opening_range_defined","close_confirmed_breakout","volume_confirmed"]
            elif key=="cumulative_volume_delta" and isinstance(item.get("cvd_change"),(int,float)) and abs(item["cvd_change"])>0:
                ev=["trade_level_cvd","directional_delta","fresh_trade_window"]
        # Pair/stat-arb signals are multi-leg portfolio trades, never single-symbol orders.
        if key in {"statistical_arbitrage", "pairs_trading"}:
            direction = "neutral"
        out.append(add(key,direction,ev))
    # Order-book imbalance is a separate, stricter named strategy over validated depth + taker flow.
    liq=(microstructure_analysis or {}).get("liquidity",{}) if isinstance(microstructure_analysis,dict) else {}
    flow=(microstructure_analysis or {}).get("order_flow",{}) if isinstance(microstructure_analysis,dict) else {}
    valid_liq=_valid_public_context(liq,market,expected_symbol,now) and _valid_public_context(flow,market,expected_symbol,now)
    ev=[]; direction="neutral"
    if valid_liq:
        try:
            di=float(liq["depth_imbalance"]); fi=float(flow["taker_flow_imbalance"]); spread=float(liq["spread_percent"])
            if all(math.isfinite(x) for x in (di,fi,spread)) and 0<=spread<=0.20 and -1<=di<=1 and -1<=fi<=1:
                if bias=="bullish" and di>=0.15 and fi>=0.15: direction="bullish"; ev=["depth_bid_imbalance","taker_buy_imbalance","spread_acceptable"]
                elif bias=="bearish" and di<=-0.15 and fi<=-0.15: direction="bearish"; ev=["depth_ask_imbalance","taker_sell_imbalance","spread_acceptable"]
        except (KeyError,TypeError,ValueError,OverflowError): pass
    out.append(add("order_book_imbalance",direction,ev))
    # Momentum burst requires volume expansion and same-direction momentum in both entry frames.
    ev=[]; direction=bias
    macd="macd_hist_positive" if bias=="bullish" else "macd_hist_negative" if bias=="bearish" else None
    if macd and macd in sig("5m") and macd in sig("15m") and "volume_spike" in sig("5m"):
        ev=["m5_m15_macd_alignment","m5_volume_expansion","higher_timeframe_bias"]
        if trend("5m")==bias and trend("15m")==bias: ev.append("entry_trend_alignment")
    out.append(add("momentum_burst",direction,ev))

    # ICT model: HTF dealing bias + liquidity raid + MSS/CHOCH + displacement + FVG.
    # OTE and kill-zone add confluence when supplied by the closed-candle/context pipeline;
    # they are not fabricated from missing data and are not mandatory in 24/7 crypto markets.
    ict_evidence = []
    ict_direction = bias if bias in {"bullish", "bearish"} else "neutral"
    aligned_htf = [frame for frame in ("1d", "4h", "1h") if trend(frame) == ict_direction]
    if ict_direction in {"bullish", "bearish"} and len(aligned_htf) >= 2:
        ict_evidence.append("ict_htf_dealing_bias")
    sweep_found = False
    mss_found = False
    fvg_found = False
    displacement_found = False
    ote_found = False
    killzone_found = False
    for frame in ("15m", "5m"):
        st = structure(frame)
        sweeps = st.get("liquidity_sweeps", []) if isinstance(st, dict) else []
        for row in sweeps[-3:] if isinstance(sweeps, list) else []:
            if isinstance(row, dict) and row.get("direction") == ict_direction and row.get("status") in {"swept", "confirmed", "valid"}:
                sweep_found = True
        shift = st.get("market_structure_shift") if isinstance(st, dict) else None
        if isinstance(shift, dict) and shift.get("direction") == ict_direction and shift.get("confirmed") is True:
            mss_found = True
        direction_signals = {
            "bullish": {"bullish_choch", "bullish_mss", "bullish_market_structure_shift"},
            "bearish": {"bearish_choch", "bearish_mss", "bearish_market_structure_shift"},
        }
        if direction_signals.get(ict_direction, set()) & sig(frame):
            mss_found = True
        gaps = st.get("fair_value_gaps", []) if isinstance(st, dict) else []
        for row in gaps[-3:] if isinstance(gaps, list) else []:
            if isinstance(row, dict) and row.get("direction") == ict_direction and row.get("status") == "open":
                fvg_found = True
                if row.get("ote_overlap") is True:
                    ote_found = True
        if ("bullish_displacement" if ict_direction == "bullish" else "bearish_displacement") in sig(frame):
            displacement_found = True
        ctx_ict = ctx.get("ict", {}) if isinstance(ctx, dict) else {}
        if isinstance(ctx_ict, dict):
            if ctx_ict.get("direction") == ict_direction and ctx_ict.get("ote_valid") is True:
                ote_found = True
            if ctx_ict.get("direction") == ict_direction and ctx_ict.get("killzone_active") is True:
                killzone_found = True
    if sweep_found: ict_evidence.append("ict_liquidity_raid")
    if mss_found: ict_evidence.append("ict_mss_or_choch")
    if displacement_found: ict_evidence.append("ict_displacement")
    if fvg_found: ict_evidence.append("ict_fair_value_gap")
    if ote_found: ict_evidence.append("ict_ote_confluence")
    if killzone_found: ict_evidence.append("ict_killzone_confluence")
    out.append(add("ict_smc_model", ict_direction, ict_evidence, min_score=5))
    allowed = {item['key'] for item in ADVANCED_STRATEGIES if market.value in item['markets']}
    return [candidate for candidate in out if candidate['key'] in allowed]
