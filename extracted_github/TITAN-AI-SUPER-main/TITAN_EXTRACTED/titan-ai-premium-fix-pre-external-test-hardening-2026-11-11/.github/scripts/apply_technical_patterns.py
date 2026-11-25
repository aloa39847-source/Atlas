from pathlib import Path
import sys

root = Path(sys.argv[1])
p = root / "app/engines/technical.py"
s = p.read_text()
if '"breakout_status"' not in s or '"morning_star"' not in s:
    old_call = "structure = self._market_structure(high, low)"
    if old_call in s:
        s = s.replace(old_call, "structure = self._market_structure(high, low, close)", 1)
    start = s.index("    def _market_structure(")
    new_methods = r"""    def _market_structure(self, highs: list[float], lows: list[float], closes: list[float] | None = None) -> dict[str, Any]:
        n = min(len(highs), len(lows))
        if n < 7:
            return {"swing_tags": [], "bias": "neutral", "bos_choch": [], "breakout_status": "unconfirmed",
                    "note": "Insufficient candles for swing structure."}

        def swings(window: int):
            sh = []
            sl = []
            for i in range(window, n - window):
                left_high = max(highs[i - window:i])
                right_high = max(highs[i + 1:i + window + 1])
                left_low = min(lows[i - window:i])
                right_low = min(lows[i + 1:i + window + 1])
                # Strict on at least one side: flat price plateaus are not repeated pivots.
                if highs[i] >= left_high and highs[i] >= right_high and (highs[i] > left_high or highs[i] > right_high):
                    sh.append(i)
                if lows[i] <= left_low and lows[i] <= right_low and (lows[i] < left_low or lows[i] < right_low):
                    sl.append(i)
            return sh, sl

        major_sh, major_sl = swings(5) if n >= 13 else ([], [])
        minor_sh, minor_sl = swings(2)
        sh, sl = minor_sh[-4:], minor_sl[-4:]
        tags: list[str] = []
        if len(sh) >= 2:
            tags.append("HH" if highs[sh[-1]] > highs[sh[-2]] else "LH")
        if len(sl) >= 2:
            tags.append("HL" if lows[sl[-1]] > lows[sl[-2]] else "LL")
        bias = "neutral"
        if "HH" in tags and "HL" in tags:
            bias = "bullish_structure"
        elif "LH" in tags and "LL" in tags:
            bias = "bearish_structure"

        last_close = float(closes[-1]) if closes else None
        last_high, last_low = float(highs[-1]), float(lows[-1])
        prior_swing_high = float(highs[sh[-1]]) if sh else None
        prior_swing_low = float(lows[sl[-1]]) if sl else None
        bos_choch: list[dict[str, Any]] = []
        breakout_status = "unconfirmed"
        if prior_swing_high is not None and last_high > prior_swing_high:
            if last_close is not None and last_close > prior_swing_high:
                breakout_status = "confirmed_bullish_breakout"
                kind = "CHOCH" if bias == "bearish_structure" else "BOS"
                bos_choch.append({"type": kind, "direction": "bullish", "level": prior_swing_high,
                                  "confirmation": "closed_above_swing_high"})
            elif last_close is not None:
                breakout_status = "false_bullish_breakout"
                bos_choch.append({"type": "LIQUIDITY_SWEEP", "direction": "bearish", "level": prior_swing_high,
                                  "confirmation": "wick_above_then_close_back_below"})
        if prior_swing_low is not None and last_low < prior_swing_low:
            if last_close is not None and last_close < prior_swing_low:
                breakout_status = "confirmed_bearish_breakout"
                kind = "CHOCH" if bias == "bullish_structure" else "BOS"
                bos_choch.append({"type": kind, "direction": "bearish", "level": prior_swing_low,
                                  "confirmation": "closed_below_swing_low"})
            elif last_close is not None:
                breakout_status = "false_bearish_breakout"
                bos_choch.append({"type": "LIQUIDITY_SWEEP", "direction": "bullish", "level": prior_swing_low,
                                  "confirmation": "wick_below_then_close_back_above"})

        return {
            "swing_tags": tags, "bias": bias, "swing_high_count": len(minor_sh), "swing_low_count": len(minor_sl),
            "major_swings": {"highs": [float(highs[i]) for i in major_sh[-4:]], "lows": [float(lows[i]) for i in major_sl[-4:]]},
            "minor_swings": {"highs": [float(highs[i]) for i in minor_sh[-4:]], "lows": [float(lows[i]) for i in minor_sl[-4:]]},
            "bos_choch": bos_choch, "breakout_status": breakout_status,
            "liquidity_zones": {"swing_highs": [float(highs[i]) for i in sh[-3:]], "swing_lows": [float(lows[i]) for i in sl[-3:]]},
            "note": "Swing/BOS/CHOCH and wick-vs-close breakout checks are heuristic; confirm with closed candles and higher-timeframe context.",
        }

    def _candle_patterns(self, df: Any) -> dict[str, Any]:
        try:
            rows = df.rows if hasattr(df, "rows") else list(df)
        except Exception:
            return {"patterns": [], "error": "invalid_candle_rows"}
        if len(rows) < 3:
            return {"patterns": [], "error": "insufficient_candles"}

        def candle(row):
            o, h, l, c = map(float, [row["open"], row["high"], row["low"], row["close"]])
            span = max(h - l, 1e-12)
            body = abs(c - o)
            upper = max(0.0, h - max(o, c))
            lower = max(0.0, min(o, c) - l)
            return {"open": o, "high": h, "low": l, "close": c, "range": span,
                    "body": body, "upper_wick": upper, "lower_wick": lower,
                    "body_to_range": body / span,
                    "close_strength": max(0.0, min(1.0, (c - l) / span)),
                    "direction": "bullish" if c > o else "bearish" if c < o else "neutral"}

        prev2, prev1, cur = candle(rows[-3]), candle(rows[-2]), candle(rows[-1])
        patterns: list[str] = []
        if cur["direction"] == "bullish": patterns.append("bullish_candle")
        elif cur["direction"] == "bearish": patterns.append("bearish_candle")
        if cur["body_to_range"] <= 0.10:
            patterns.append("doji")

        prior_closes = [float(r["close"]) for r in rows[-7:-1]]
        prior_trend = "sideways"
        if len(prior_closes) >= 4:
            delta = prior_closes[-1] - prior_closes[0]
            if delta > abs(prior_closes[0]) * 0.002: prior_trend = "uptrend"
            elif delta < -abs(prior_closes[0]) * 0.002: prior_trend = "downtrend"

        small_body = cur["body"] <= cur["range"] * 0.35
        long_lower = cur["lower_wick"] >= max(cur["body"] * 2.0, cur["range"] * 0.35)
        long_upper = cur["upper_wick"] >= max(cur["body"] * 2.0, cur["range"] * 0.35)
        if small_body and long_lower and cur["upper_wick"] <= max(cur["body"], cur["range"] * 0.12):
            patterns.append("hammer" if prior_trend == "downtrend" else "hanging_man" if prior_trend == "uptrend" else "hammer_like")
        if small_body and long_upper and cur["lower_wick"] <= max(cur["body"], cur["range"] * 0.12):
            patterns.append("shooting_star" if prior_trend == "uptrend" else "shooting_star_like")
        if small_body and max(cur["upper_wick"], cur["lower_wick"]) >= cur["body"] * 2.0:
            patterns.append("pin_bar")
            if cur["lower_wick"] > cur["upper_wick"]: patterns.append("bullish_pin_bar")
            elif cur["upper_wick"] > cur["lower_wick"]: patterns.append("bearish_pin_bar")

        if prev1["direction"] == "bearish" and cur["direction"] == "bullish" and cur["close"] >= prev1["open"] and cur["open"] <= prev1["close"]:
            patterns.append("bullish_engulfing")
        if prev1["direction"] == "bullish" and cur["direction"] == "bearish" and cur["open"] >= prev1["close"] and cur["close"] <= prev1["open"]:
            patterns.append("bearish_engulfing")

        # Crypto trades continuously: gap rules are relaxed, but the small middle body and midpoint close remain required.
        first_mid = (prev2["open"] + prev2["close"]) / 2.0
        first_large = prev2["body_to_range"] >= 0.45
        middle_small = prev1["body"] <= max(prev2["body"] * 0.45, prev2["range"] * 0.12)
        if prev2["direction"] == "bearish" and first_large and middle_small and cur["direction"] == "bullish" and cur["close"] > first_mid:
            patterns.append("morning_star")
        if prev2["direction"] == "bullish" and first_large and middle_small and cur["direction"] == "bearish" and cur["close"] < first_mid:
            patterns.append("evening_star")

        return {
            "patterns": list(dict.fromkeys(patterns)),
            "body_to_range": round(cur["body_to_range"], 4),
            "upper_wick_to_range": round(cur["upper_wick"] / cur["range"], 4),
            "lower_wick_to_range": round(cur["lower_wick"] / cur["range"], 4),
            "upper_wick_to_body": round(cur["upper_wick"] / max(cur["body"], 1e-12), 4),
            "lower_wick_to_body": round(cur["lower_wick"] / max(cur["body"], 1e-12), 4),
            "close_strength": round(cur["close_strength"], 4),
            "candle_direction": cur["direction"],
            "prior_trend_context": prior_trend,
            "note": "Pattern labels are heuristic; location, trend, volume, volatility and candle-close confirmation matter.",
        }
"""
    s = s[:start] + new_methods + "\n"
    p.write_text(s)

(root / "tests/test_technical_patterns.py").write_text(r'''from app.core.enums import Timeframe
from app.engines.market_data import Candles
from app.engines.technical import TechnicalAnalysisEngine


def _base_rows(n=30, start=100.0, direction=0.0):
    rows = []
    price = start
    for _ in range(n):
        close = price + direction
        rows.append({"open": price, "high": max(price, close) + 0.1,
                     "low": min(price, close) - 0.1, "close": close, "volume": 100.0})
        price = close
    return rows


def _patterns(rows):
    return TechnicalAnalysisEngine().analyze(Candles(rows), Timeframe.M5).candles["patterns"]


def test_hammer_pattern_after_downtrend():
    rows = _base_rows(start=20.0, direction=-0.2)
    rows[-1] = {"open": 14.2, "high": 14.28, "low": 13.6, "close": 14.3, "volume": 120}
    assert "hammer" in _patterns(rows)


def test_hanging_man_pattern_after_uptrend():
    rows = _base_rows(start=10.0, direction=0.2)
    rows[-1] = {"open": 15.2, "high": 15.28, "low": 14.6, "close": 15.3, "volume": 120}
    assert "hanging_man" in _patterns(rows)


def test_shooting_star_after_uptrend():
    rows = _base_rows(start=10.0, direction=0.2)
    rows[-1] = {"open": 15.2, "high": 15.9, "low": 15.12, "close": 15.1, "volume": 120}
    assert "shooting_star" in _patterns(rows)


def test_doji_and_close_strength_are_reported():
    rows = _base_rows()
    rows[-1] = {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.02, "volume": 100}
    result = TechnicalAnalysisEngine().analyze(Candles(rows), Timeframe.M5).candles
    assert "doji" in result["patterns"]
    assert 0.0 <= result["close_strength"] <= 1.0
    assert "upper_wick_to_body" in result and "lower_wick_to_body" in result


def test_morning_star_three_candle_pattern():
    rows = _base_rows()
    rows[-3] = {"open": 10.0, "high": 10.1, "low": 7.9, "close": 8.0, "volume": 100}
    rows[-2] = {"open": 8.0, "high": 8.2, "low": 7.8, "close": 8.05, "volume": 100}
    rows[-1] = {"open": 8.1, "high": 9.3, "low": 8.0, "close": 9.2, "volume": 100}
    assert "morning_star" in _patterns(rows)


def test_evening_star_three_candle_pattern():
    rows = _base_rows()
    rows[-3] = {"open": 8.0, "high": 10.1, "low": 7.9, "close": 10.0, "volume": 100}
    rows[-2] = {"open": 10.0, "high": 10.2, "low": 9.8, "close": 10.05, "volume": 100}
    rows[-1] = {"open": 9.9, "high": 10.0, "low": 8.7, "close": 8.8, "volume": 100}
    assert "evening_star" in _patterns(rows)


def test_structure_marks_close_confirmed_breakout_as_bos_when_bias_is_not_opposite():
    engine = TechnicalAnalysisEngine()
    highs = [10.0] * 20
    lows = [8.0] * 20
    closes = [9.0] * 20
    highs[4] = 12.0
    highs[10] = 11.0
    lows[6] = 6.0
    lows[13] = 7.0
    highs[-1] = 12.5
    closes[-1] = 12.3
    lows[-1] = 9.0
    result = engine._market_structure(highs, lows, closes)
    assert result["breakout_status"] == "confirmed_bullish_breakout"
    assert any(x["type"] == "BOS" and x["direction"] == "bullish" for x in result["bos_choch"])


def test_structure_marks_countertrend_breakout_as_choch():
    engine = TechnicalAnalysisEngine()
    highs = [10.0] * 20
    lows = [8.0] * 20
    closes = [9.0] * 20
    highs[4] = 12.0
    highs[10] = 11.0
    lows[6] = 7.0
    lows[13] = 6.0
    highs[-1] = 12.5
    closes[-1] = 12.3
    lows[-1] = 8.0
    result = engine._market_structure(highs, lows, closes)
    assert result["bias"] == "bearish_structure"
    assert result["breakout_status"] == "confirmed_bullish_breakout"
    assert any(x["type"] == "CHOCH" and x["direction"] == "bullish" for x in result["bos_choch"])


def test_structure_distinguishes_wick_only_false_breakout():
    engine = TechnicalAnalysisEngine()
    highs = [10.0] * 20
    lows = [8.0] * 20
    closes = [9.0] * 20
    highs[4] = 12.0
    highs[10] = 11.0
    lows[6] = 6.0
    lows[13] = 7.0
    highs[-1] = 12.0
    closes[-1] = 10.5
    lows[-1] = 9.0
    result = engine._market_structure(highs, lows, closes)
    assert result["breakout_status"] == "false_bullish_breakout"
    assert any(x["type"] == "LIQUIDITY_SWEEP" for x in result["bos_choch"])
''')
