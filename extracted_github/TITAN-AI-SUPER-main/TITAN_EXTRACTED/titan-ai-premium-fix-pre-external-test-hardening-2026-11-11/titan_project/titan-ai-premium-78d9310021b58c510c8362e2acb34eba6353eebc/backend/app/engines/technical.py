from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from app.core.enums import EngineStatus, Timeframe
from .base import Engine


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        fv = float(v)
        return None if math.isnan(fv) or math.isinf(fv) else fv
    except Exception:
        return None


def _sma(vals: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(vals)
    if n <= 0:
        return out
    s = 0.0
    for i, v in enumerate(vals):
        s += float(v)
        if i >= n:
            s -= float(vals[i - n])
        if i >= n - 1:
            out[i] = s / n
    return out


def _ema(vals: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(vals)
    if n <= 0 or len(vals) < n:
        return out
    alpha = 2.0 / (n + 1.0)
    seed = sum(float(x) for x in vals[:n]) / n
    out[n - 1] = seed
    prev = seed
    for i in range(n, len(vals)):
        prev = alpha * float(vals[i]) + (1 - alpha) * prev
        out[i] = prev
    return out


def _ema_wilder(vals: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(vals)
    if n <= 0 or len(vals) < n:
        return out
    alpha = 1.0 / n
    seed = sum(float(x) for x in vals[:n]) / n
    out[n - 1] = seed
    prev = seed
    for i in range(n, len(vals)):
        prev = alpha * float(vals[i]) + (1 - alpha) * prev
        out[i] = prev
    return out


def _rsi(close: Sequence[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(close)
    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
        return out
    try:
        prices = [float(value) for value in close]
    except (TypeError, ValueError, OverflowError):
        return out
    if not all(math.isfinite(value) for value in prices) or len(prices) < n + 1:
        return out
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_g = _ema_wilder(gains, n)
    avg_l = _ema_wilder(losses, n)
    for i in range(len(avg_g)):
        g, l = avg_g[i], avg_l[i]
        if g is None or l is None:
            continue
        if l == 0 and g == 0:
            out[i + 1] = 50.0
        elif l == 0:
            out[i + 1] = 100.0
        elif g == 0:
            out[i + 1] = 0.0
        else:
            rs = g / l
            out[i + 1] = 100.0 - (100.0 / (1.0 + rs))
    return out


def _atr(high: Sequence[float], low: Sequence[float], close: Sequence[float], n: int = 14) -> list[float | None]:
    trs: list[float] = []
    for i in range(len(close)):
        if i == 0:
            trs.append(float(high[i]) - float(low[i]))
        else:
            pc = float(close[i - 1])
            trs.append(max(float(high[i]) - float(low[i]), abs(float(high[i]) - pc), abs(float(low[i]) - pc)))
    return _ema_wilder(trs, n)


def _macd(close: Sequence[float]) -> dict[str, list[float | None]]:
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    line: list[float | None] = [None] * len(close)
    vals = []
    idxs = []
    for i in range(len(close)):
        if ema12[i] is not None and ema26[i] is not None:
            v = ema12[i] - ema26[i]  # type: ignore
            line[i] = v
            vals.append(v)
            idxs.append(i)
    signal_vals = _ema(vals, 9) if vals else []
    signal: list[float | None] = [None] * len(close)
    hist: list[float | None] = [None] * len(close)
    for j, i in enumerate(idxs):
        sv = signal_vals[j] if j < len(signal_vals) else None
        signal[i] = sv
        if line[i] is not None and sv is not None:
            hist[i] = line[i] - sv  # type: ignore
    return {"macd": line, "macd_signal": signal, "macd_hist": hist}



def _stoch_rsi(close: Sequence[float], period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> dict[str, list[float | None]]:
    rsi = _rsi(close, period)
    raw: list[float | None] = [None] * len(close)
    for i in range(len(close)):
        window = [v for v in rsi[max(0, i-period+1):i+1] if v is not None]
        if len(window) == period and max(window) != min(window):
            raw[i] = (rsi[i] - min(window)) / (max(window)-min(window)) * 100
        elif len(window) == period:
            raw[i] = 50.0
    def smooth(values, n):
        out=[None]*len(values)
        for i in range(n-1,len(values)):
            w=values[i-n+1:i+1]
            if all(v is not None and math.isfinite(float(v)) for v in w): out[i]=sum(w)/n
        return out
    k=smooth(raw,smooth_k)
    return {"stoch_rsi_k":k,"stoch_rsi_d":smooth(k,smooth_d)}


def _cci(high: Sequence[float], low: Sequence[float], close: Sequence[float], n: int = 20) -> list[float | None]:
    typical=[(float(h)+float(l)+float(c))/3 for h,l,c in zip(high,low,close)]
    mean=_sma(typical,n); out=[None]*len(typical)
    for i in range(n-1,len(typical)):
        w=typical[i-n+1:i+1]; md=sum(abs(x-mean[i]) for x in w)/n
        if md: out[i]=(typical[i]-mean[i])/(0.015*md)
    return out


def _vwap(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    volume: Sequence[float],
    timestamps: Sequence[Any] | None = None,
) -> list[float | None]:
    """UTC-session VWAP; without timestamps, use the supplied batch as one anchor."""
    from datetime import datetime, timezone

    out: list[float | None] = []
    pv = 0.0
    vv = 0.0
    previous_session = None
    for index, (h, l, c, v) in enumerate(zip(high, low, close, volume)):
        session = None
        if timestamps is not None and index < len(timestamps):
            raw = timestamps[index]
            try:
                if isinstance(raw, datetime):
                    stamp = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
                elif isinstance(raw, bool):
                    raise ValueError("invalid_timestamp")
                elif isinstance(raw, (int, float)):
                    numeric = float(raw)
                    if not math.isfinite(numeric):
                        raise ValueError("invalid_timestamp")
                    # Binance timestamps are milliseconds; accept seconds too.
                    stamp = datetime.fromtimestamp(numeric / 1000.0 if numeric > 10**11 else numeric, timezone.utc)
                else:
                    stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=timezone.utc)
                session = stamp.astimezone(timezone.utc).date()
            except (TypeError, ValueError, OverflowError, OSError):
                session = None
        if session is not None and previous_session is not None and session != previous_session:
            pv = 0.0
            vv = 0.0
        typical = (float(h) + float(l) + float(c)) / 3
        pv += typical * float(v)
        vv += float(v)
        out.append(pv / vv if vv > 0 else None)
        if session is not None:
            previous_session = session
    return out


def _supertrend(high: Sequence[float], low: Sequence[float], close: Sequence[float], atr: Sequence[float | None], multiplier: float=3.0) -> dict[str,list[float | str | None]]:
    upper=[None]*len(close); lower=[None]*len(close); line=[None]*len(close); direction=[None]*len(close)
    for i in range(len(close)):
        a=atr[i]
        if a is None: continue
        mid=(float(high[i])+float(low[i]))/2
        bu=mid+multiplier*a; bl=mid-multiplier*a
        if i==0 or upper[i-1] is None:
            upper[i]=bu; lower[i]=bl; direction[i]="bullish" if close[i]>=mid else "bearish"
        else:
            upper[i]=bu if bu<float(upper[i-1]) or close[i-1]>float(upper[i-1]) else upper[i-1]
            lower[i]=bl if bl>float(lower[i-1]) or close[i-1]<float(lower[i-1]) else lower[i-1]
            prev=direction[i-1]
            direction[i]="bullish" if prev=="bearish" and close[i]>float(upper[i-1]) else "bearish" if prev=="bullish" and close[i]<float(lower[i-1]) else prev
        line[i]=lower[i] if direction[i]=="bullish" else upper[i]
    return {"supertrend":line,"supertrend_direction":direction}


def _ichimoku(high: Sequence[float], low: Sequence[float]) -> dict[str,list[float | None]]:
    def midpoint(n):
        out=[None]*len(high)
        for i in range(n-1,len(high)):
            out[i]=(max(high[i-n+1:i+1])+min(low[i-n+1:i+1]))/2
        return out
    tenkan=midpoint(9); kijun=midpoint(26); raw_span_b=midpoint(52)
    raw_span_a=[(a+b)/2 if a is not None and b is not None else None for a,b in zip(tenkan,kijun)]
    # Senkou spans are plotted 26 periods forward; align the currently visible
    # cloud to the candle at index i using values calculated 26 candles earlier.
    span_a=[None]*26 + raw_span_a[:-26] if len(raw_span_a)>26 else [None]*len(raw_span_a)
    span_b=[None]*26 + raw_span_b[:-26] if len(raw_span_b)>26 else [None]*len(raw_span_b)
    return {"ichimoku_tenkan":tenkan,"ichimoku_kijun":kijun,"ichimoku_span_a":span_a,"ichimoku_span_b":span_b}


def _adx(high: Sequence[float], low: Sequence[float], close: Sequence[float], n: int = 14) -> dict[str, list[float | None]]:
    size = len(close)
    plus_dm = [0.0] * size
    minus_dm = [0.0] * size
    tr = [0.0] * size
    for i in range(size):
        if i == 0:
            tr[i] = float(high[i]) - float(low[i])
            continue
        up = float(high[i]) - float(high[i-1])
        down = float(low[i-1]) - float(low[i])
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
        tr[i] = max(float(high[i])-float(low[i]), abs(float(high[i])-float(close[i-1])), abs(float(low[i])-float(close[i-1])))
    atr = _ema_wilder(tr, n)
    plus_sm = _ema_wilder(plus_dm, n)
    minus_sm = _ema_wilder(minus_dm, n)
    plus_di = [None] * size
    minus_di = [None] * size
    dx = [None] * size
    for i in range(size):
        if atr[i] is None or atr[i] <= 0 or plus_sm[i] is None or minus_sm[i] is None:
            continue
        plus_di[i] = 100.0 * plus_sm[i] / atr[i]
        minus_di[i] = 100.0 * minus_sm[i] / atr[i]
        denom = plus_di[i] + minus_di[i]
        dx[i] = 100.0 * abs(plus_di[i] - minus_di[i]) / denom if denom else 0.0
    valid_dx = [float(v) if v is not None else 0.0 for v in dx]
    adx = _ema_wilder(valid_dx, n)
    return {"adx14": adx, "plus_di14": plus_di, "minus_di14": minus_di}


def _parabolic_sar(high: Sequence[float], low: Sequence[float], step: float = 0.02, maximum: float = 0.2) -> dict[str, list[float | str | None]]:
    size = len(high)
    sar: list[float | None] = [None] * size
    direction: list[str | None] = [None] * size
    if size < 2:
        return {"parabolic_sar": sar, "parabolic_sar_direction": direction}
    bullish = float(high[1]) + float(low[1]) >= float(high[0]) + float(low[0])
    ep = max(float(high[0]), float(high[1])) if bullish else min(float(low[0]), float(low[1]))
    sar[1] = min(float(low[0]), float(low[1])) if bullish else max(float(high[0]), float(high[1]))
    af = step
    direction[1] = "bullish" if bullish else "bearish"
    for i in range(2, size):
        previous = float(sar[i-1])
        current = previous + af * (ep - previous)
        if bullish:
            current = min(current, float(low[i-1]), float(low[i-2]))
            if float(low[i]) < current:
                bullish = False
                current = ep
                ep = float(low[i])
                af = step
            elif float(high[i]) > ep:
                ep = float(high[i])
                af = min(maximum, af + step)
        else:
            current = max(current, float(high[i-1]), float(high[i-2]))
            if float(high[i]) > current:
                bullish = True
                current = ep
                ep = float(high[i])
                af = step
            elif float(low[i]) < ep:
                ep = float(low[i])
                af = min(maximum, af + step)
        sar[i] = current
        direction[i] = "bullish" if bullish else "bearish"
    return {"parabolic_sar": sar, "parabolic_sar_direction": direction}


def _volume_profile(high: Sequence[float], low: Sequence[float], close: Sequence[float],
                    volume: Sequence[float], bins: int = 24) -> dict[str, float | None]:
    """Approximate candle-volume profile; allocates each candle's volume to its typical price bin."""
    if not close or not volume or len(close) != len(volume):
        return {"volume_profile_poc": None, "volume_profile_vah": None, "volume_profile_val": None}
    lo, hi = min(float(x) for x in low), max(float(x) for x in high)
    total = sum(float(v) for v in volume)
    if hi <= lo or total <= 0:
        return {"volume_profile_poc": None, "volume_profile_vah": None, "volume_profile_val": None}
    width = (hi - lo) / bins
    buckets = [0.0] * bins
    for h, l, c, v in zip(high, low, close, volume):
        if float(v) <= 0:
            continue
        typical = (float(h) + float(l) + float(c)) / 3.0
        index = max(0, min(bins - 1, int((typical - lo) / width)))
        buckets[index] += float(v)
    poc_index = max(range(bins), key=lambda i: buckets[i])
    target = sum(buckets) * 0.70
    left = right = poc_index
    accumulated = buckets[poc_index]
    while accumulated < target and (left > 0 or right < bins - 1):
        left_volume = buckets[left-1] if left > 0 else -1
        right_volume = buckets[right+1] if right < bins-1 else -1
        if right_volume > left_volume:
            right += 1
            accumulated += buckets[right]
        else:
            left -= 1
            accumulated += buckets[left]
    center = lambda i: lo + (i + 0.5) * width
    return {"volume_profile_poc": center(poc_index), "volume_profile_vah": lo + (right + 1) * width,
            "volume_profile_val": lo + left * width}


def _last(seq: Sequence[Any]) -> Any:
    return seq[-1] if seq else None


def _rolling_std(vals: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(vals)
    for i in range(n - 1, len(vals)):
        w = [float(x) for x in vals[i - n + 1 : i + 1]]
        m = sum(w) / n
        var = sum((x - m) ** 2 for x in w) / n
        out[i] = math.sqrt(var)
    return out


@dataclass
class TimeframeAnalysis:
    timeframe: Timeframe
    trend: str
    signals: list[str]
    indicators: dict[str, Any]
    structure: dict[str, Any]
    candles: dict[str, Any]


class TechnicalAnalysisEngine(Engine):
    key = "technical_analysis"
    name = "Technical Analysis Engine"

    def status(self) -> EngineStatus:
        return EngineStatus.IMPLEMENTED_PARTIAL

    def analyze(self, df: Any, timeframe: Timeframe) -> TimeframeAnalysis:
        # df: Candles container or compatible (supports ["close"] lists, len, iloc)
        try:
            columns = getattr(df, "columns", [])
            open_prices = [float(x) for x in df["open"]]
            high = [float(x) for x in df["high"]]
            low = [float(x) for x in df["low"]]
            close = [float(x) for x in df["close"]]
            vol = [float(x) for x in df["volume"]] if "volume" in columns else [0.0] * len(close)
            timestamps = list(df["timestamp"]) if "timestamp" in columns else None
        except Exception:
            return TimeframeAnalysis(timeframe, "unknown", ["invalid_data"], {}, {}, {})
        if len(close) < 30:
            return TimeframeAnalysis(timeframe, "unknown", ["insufficient_data"], {}, {}, {})
        if not (len(open_prices) == len(high) == len(low) == len(close) == len(vol)):
            return TimeframeAnalysis(timeframe, "unknown", ["invalid_data_lengths"], {}, {}, {})
        if (not all(math.isfinite(value) for series in (open_prices, high, low, close, vol) for value in series)
                or any(value <= 0 for series in (open_prices, high, low, close) for value in series)
                or any(value < 0 for value in vol)
                or any(h < max(o, c, l) or l > min(o, c, h)
                       for o, h, l, c in zip(open_prices, high, low, close))):
            return TimeframeAnalysis(timeframe, "unknown", ["invalid_ohlcv_integrity"], {}, {}, {})

        rsi = _rsi(close)
        macd = _macd(close)
        bb_mid = _sma(close, 20)
        bb_std = _rolling_std(close, 20)
        atr = _atr(high, low, close)
        ema9 = _ema(close, 9)
        ema20 = _ema(close, 20)
        ema21 = _ema(close, 21)
        ema50 = _ema(close, 50)
        ema200 = _ema(close, 200)
        sma20 = _sma(close, 20)
        sma50 = _sma(close, 50)
        vol_sma20 = _sma(vol, 20)
        vwap = _vwap(high, low, close, vol, timestamps)
        stoch = _stoch_rsi(close)
        cci = _cci(high, low, close)
        st = _supertrend(high, low, close, atr)
        ichi = _ichimoku(high, low)
        adx_data = _adx(high, low, close)
        psar_data = _parabolic_sar(high, low)
        profile = _volume_profile(high, low, close, vol)

        bbm = _last(bb_mid); bbs = _last(bb_std)
        last = {
            "close": _f(close[-1]),
            "rsi14": _f(_last(rsi)),
            "macd": _f(_last(macd["macd"])),
            "macd_signal": _f(_last(macd["macd_signal"])),
            "macd_hist": _f(_last(macd["macd_hist"])),
            "ema9": _f(_last(ema9)),
            "ema20": _f(_last(ema20)),
            "ema21": _f(_last(ema21)),
            "ema50": _f(_last(ema50)),
            "ema200": _f(_last(ema200)),
            "sma20": _f(_last(sma20)),
            "sma50": _f(_last(sma50)),
            "vwap": _f(_last(vwap)),
            "stoch_rsi_k": _f(_last(stoch["stoch_rsi_k"])),
            "stoch_rsi_d": _f(_last(stoch["stoch_rsi_d"])),
            "cci20": _f(_last(cci)),
            "supertrend": _f(_last(st["supertrend"])),
            "supertrend_direction": _last(st["supertrend_direction"]),
            "ichimoku_tenkan": _f(_last(ichi["ichimoku_tenkan"])),
            "ichimoku_kijun": _f(_last(ichi["ichimoku_kijun"])),
            "ichimoku_span_a": _f(_last(ichi["ichimoku_span_a"])),
            "ichimoku_span_b": _f(_last(ichi["ichimoku_span_b"])),
            "bb_upper": _f(bbm + 2 * bbs) if bbm is not None and bbs is not None else None,
            "bb_mid": _f(bbm),
            "bb_lower": _f(bbm - 2 * bbs) if bbm is not None and bbs is not None else None,
            "atr14": _f(_last(atr)),
            "volume_last": _f(vol[-1]),
            "volume_sma20": _f(_last(vol_sma20)),
            "volume_ratio": _f((vol[-1] / vol_sma20[-1]) if vol_sma20 and vol_sma20[-1] not in (None, 0) else None),
            "adx14": _f(_last(adx_data["adx14"])),
            "plus_di14": _f(_last(adx_data["plus_di14"])),
            "minus_di14": _f(_last(adx_data["minus_di14"])),
            "parabolic_sar": _f(_last(psar_data["parabolic_sar"])),
            "parabolic_sar_direction": _last(psar_data["parabolic_sar_direction"]),
            **profile,
        }

        signals: list[str] = []
        trend = "sideways"
        e20, e50 = last["ema20"], last["ema50"]
        c = last["close"] or 0
        if e20 is not None and e50 is not None:
            if c > e20 > e50:
                trend = "bullish"; signals.append("price_above_ema20_ema50")
            elif c < e20 < e50:
                trend = "bearish"; signals.append("price_below_ema20_ema50")
        if last["ema9"] is not None and last["ema21"] is not None:
            if last["ema9"] > last["ema21"]: signals.append("ema9_above_ema21")
            elif last["ema9"] < last["ema21"]: signals.append("ema9_below_ema21")
        if last["ema200"] is not None:
            if c > last["ema200"]: signals.append("price_above_ema200")
            elif c < last["ema200"]: signals.append("price_below_ema200")
        if last["vwap"] is not None:
            signals.append("price_above_vwap" if c > last["vwap"] else "price_below_vwap")
        if last["parabolic_sar_direction"] in {"bullish","bearish"}:
            signals.append("parabolic_sar_" + last["parabolic_sar_direction"])
        if last["adx14"] is not None and last["adx14"] >= 20:
            if last["plus_di14"] is not None and last["minus_di14"] is not None:
                if last["plus_di14"] > last["minus_di14"]: signals.append("adx_trend_bullish")
                elif last["minus_di14"] > last["plus_di14"]: signals.append("adx_trend_bearish")
        if last["volume_profile_poc"] is not None:
            signals.append("volume_profile_above_poc" if c > last["volume_profile_poc"] else "volume_profile_below_poc")
        if last["supertrend_direction"] in {"bullish","bearish"}:
            signals.append("supertrend_" + last["supertrend_direction"])
        if last["ichimoku_span_a"] is not None and last["ichimoku_span_b"] is not None:
            cloud_top=max(last["ichimoku_span_a"],last["ichimoku_span_b"])
            cloud_bottom=min(last["ichimoku_span_a"],last["ichimoku_span_b"])
            if c > cloud_top: signals.append("price_above_ichimoku_cloud")
            elif c < cloud_bottom: signals.append("price_below_ichimoku_cloud")
        if last["stoch_rsi_k"] is not None:
            if last["stoch_rsi_k"] < 20: signals.append("stoch_rsi_oversold")
            elif last["stoch_rsi_k"] > 80: signals.append("stoch_rsi_overbought")
        if last["cci20"] is not None:
            if last["cci20"] < -100: signals.append("cci_oversold")
            elif last["cci20"] > 100: signals.append("cci_overbought")
        rv = last["rsi14"]
        if rv is not None:
            if rv < 30: signals.append("rsi_oversold")
            elif rv > 70: signals.append("rsi_overbought")
        mh = last["macd_hist"]
        if mh is not None:
            signals.append("macd_hist_positive" if mh > 0 else "macd_hist_negative")

        structure = self._market_structure(high, low, close, open_prices=open_prices, volumes=vol)
        for sweep in structure.get("liquidity_sweeps", []):
            signals.append("liquidity_sweep_" + str(sweep.get("direction")))
        for gap in structure.get("fair_value_gaps", []):
            signals.append("fvg_" + str(gap.get("direction")) + "_open")
        for block in structure.get("order_blocks", []):
            signals.append("order_block_" + str(block.get("direction")) + "_candidate")
        # Expose only close-confirmed structural events as strategy signals.
        # Wick-only excursions remain recorded as false breakouts in evidence.
        for breakout in structure.get("breakouts", []):
            if breakout.get("status") == "confirmed":
                signals.append(f"breakout_{breakout.get('direction')}")
        for event in structure.get("bos_choch", []):
            event_type = str(event.get("type") or "").lower()
            direction = str(event.get("direction") or "").lower()
            if event_type in {"bos", "choch"} and direction in {"bullish", "bearish"}:
                signals.append(f"{event_type}_{direction}")
        candles = self._candle_patterns(df)
        signals.extend(candles.get("patterns", []))
        volume_ratio = last.get("volume_ratio")
        if volume_ratio is not None and volume_ratio >= 1.5:
            signals.append("volume_spike")
        elif volume_ratio is not None and volume_ratio <= 0.5:
            signals.append("volume_below_average")
        if last["bb_upper"] is not None and c >= last["bb_upper"]:
            signals.append("bollinger_upper_band_touch")
        elif last["bb_lower"] is not None and c <= last["bb_lower"]:
            signals.append("bollinger_lower_band_touch")
        return TimeframeAnalysis(timeframe, trend, signals, last, structure, candles)

    def _market_structure(self, highs: list[float], lows: list[float], closes: list[float] | None = None, *, open_prices: list[float] | None = None, volumes: list[float] | None = None) -> dict[str, Any]:
        n = min(len(highs), len(lows))
        window = 3
        swing_highs = [i for i in range(window, n - window) if highs[i] == max(highs[i - window : i + window + 1])]
        swing_lows = [i for i in range(window, n - window) if lows[i] == min(lows[i - window : i + window + 1])]
        sh = swing_highs[-4:]; sl = swing_lows[-4:]
        high_tag = "HH" if len(sh) >= 2 and highs[sh[-1]] > highs[sh[-2]] else "LH" if len(sh) >= 2 else None
        low_tag = "HL" if len(sl) >= 2 and lows[sl[-1]] > lows[sl[-2]] else "LL" if len(sl) >= 2 else None
        tags = [x for x in (high_tag, low_tag) if x]
        bias = "neutral"
        if high_tag == "HH" and low_tag == "HL": bias = "bullish_structure"
        elif high_tag == "LH" and low_tag == "LL": bias = "bearish_structure"
        bos_choch: list[dict[str, Any]] = []
        breakouts: list[dict[str, Any]] = []
        if closes and len(closes) >= 1:
            last_close, last_high, last_low = float(closes[-1]), float(highs[-1]), float(lows[-1])
            if sh:
                level = float(highs[sh[-1]])
                if last_high > level:
                    confirmed = last_close > level
                    breakouts.append({"direction": "bullish", "level": level, "confirmed_by_close": confirmed,
                                      "status": "confirmed" if confirmed else "false_breakout", "wick_extreme": last_high, "close": last_close})
                    if confirmed:
                        kind = "CHOCH" if bias == "bearish_structure" else "BOS"
                        bos_choch.append({"type": kind, "direction": "bullish", "level": level})
            if sl:
                level = float(lows[sl[-1]])
                if last_low < level:
                    confirmed = last_close < level
                    breakouts.append({"direction": "bearish", "level": level, "confirmed_by_close": confirmed,
                                      "status": "confirmed" if confirmed else "false_breakout", "wick_extreme": last_low, "close": last_close})
                    if confirmed:
                        kind = "CHOCH" if bias == "bullish_structure" else "BOS"
                        bos_choch.append({"type": kind, "direction": "bearish", "level": level})
        # SMC features are confirmed only on the latest completed candle.
        # A pivot at p becomes usable at p + window; never label it before then.
        current_index = n - 1
        current_close = float(closes[-1]) if closes else float(highs[-1]+lows[-1])/2
        liquidity_sweeps = []
        if sh:
            level = float(highs[sh[-1]])
            if float(highs[-1]) > level and current_close < level:
                liquidity_sweeps.append({"type":"buy_side_liquidity_sweep","direction":"bearish",
                    "level":level,"wick":float(highs[-1]),"close":current_close,
                    "pivot_index":sh[-1],"pivot_confirmed_at":sh[-1]+window,
                    "confirmation_index":current_index})
        if sl:
            level = float(lows[sl[-1]])
            if float(lows[-1]) < level and current_close > level:
                liquidity_sweeps.append({"type":"sell_side_liquidity_sweep","direction":"bullish",
                    "level":level,"wick":float(lows[-1]),"close":current_close,
                    "pivot_index":sl[-1],"pivot_confirmed_at":sl[-1]+window,
                    "confirmation_index":current_index})

        fair_value_gaps = []
        if n >= 3 and closes:
            # Three-candle imbalance; only gaps still unfilled as of the current bar survive.
            for i in range(max(2, n-80), n):
                if float(lows[i]) > float(highs[i-2]):
                    lower, upper = float(highs[i-2]), float(lows[i])
                    filled = any(float(lows[j]) <= lower for j in range(i+1,n))
                    if not filled:
                        fair_value_gaps.append({"direction":"bullish","lower":lower,"upper":upper,
                            "origin_index":i,"confirmed_at":i,"status":"open"})
                elif float(highs[i]) < float(lows[i-2]):
                    lower, upper = float(highs[i]), float(lows[i-2])
                    filled = any(float(highs[j]) >= upper for j in range(i+1,n))
                    if not filled:
                        fair_value_gaps.append({"direction":"bearish","lower":lower,"upper":upper,
                            "origin_index":i,"confirmed_at":i,"status":"open"})
        fair_value_gaps = fair_value_gaps[-10:]

        order_blocks = []
        if closes and open_prices and len(open_prices) == n and n >= 15:
            true_ranges=[]
            for i in range(1,n):
                true_ranges.append(max(float(highs[i])-float(lows[i]),
                    abs(float(highs[i])-float(closes[i-1])),
                    abs(float(lows[i])-float(closes[i-1]))))
            atr = sum(true_ranges[-14:])/min(14,len(true_ranges)) if true_ranges else 0.0
            if atr > 0:
                for direction in ("bullish","bearish"):
                    displaced = current_close-float(open_prices[-1])
                    valid = displaced >= 1.5*atr if direction=="bullish" else displaced <= -1.5*atr
                    structure_break = any(e["direction"]==direction for e in bos_choch)
                    if valid and structure_break:
                        for j in range(n-2,max(-1,n-13),-1):
                            opposite = (float(closes[j]) < float(open_prices[j])) if direction=="bullish" else (float(closes[j]) > float(open_prices[j]))
                            if opposite:
                                order_blocks.append({"direction":direction,"lower":float(lows[j]),
                                    "upper":float(highs[j]),"origin_index":j,
                                    "confirmation_index":current_index,"status":"candidate",
                                    "displacement_atr":round(abs(displaced)/atr,3)})
                                break
        order_blocks = order_blocks[-6:]
        if volumes is not None and len(volumes)==n and volumes:
            vol_window=[float(v) for v in volumes[-21:-1] if math.isfinite(float(v))]
            volume_baseline=sum(vol_window)/len(vol_window) if vol_window else 0.0
            displacement_volume_ratio=(float(volumes[-1])/volume_baseline) if volume_baseline>0 else None
        else:
            displacement_volume_ratio=None
        regime_closes=[float(x) for x in (closes[-20:] if closes else [])]
        regime_highs=[float(x) for x in highs[-20:]]
        regime_lows=[float(x) for x in lows[-20:]]
        if regime_closes:
            net_move=regime_closes[-1]-regime_closes[0]
            mean_price=sum(regime_closes)/len(regime_closes)
            full_range=max(regime_highs)-min(regime_lows)
            efficiency=abs(net_move)/max(full_range,1e-12)
            net_change_pct=net_move/max(mean_price,1e-12)
            if abs(net_change_pct)>=0.01 and efficiency>=0.55:
                regime_trend="trending_bullish" if net_move>0 else "trending_bearish"
            else:
                regime_trend="ranging"
            regime_atr=sum(true_ranges[-14:])/min(14,len(true_ranges)) if 'true_ranges' in locals() and true_ranges else 0.0
            volatility_pct=regime_atr/max(mean_price,1e-12)
            volatility_regime="high_volatility" if volatility_pct>=0.02 else "normal_volatility"
        else:
            efficiency=0.0;net_change_pct=0.0;regime_trend="unknown"
            volatility_pct=0.0;volatility_regime="unknown"

        def swing_points(indices: list[int], values: list[float]) -> list[dict[str, Any]]:
            return [{"index": i, "price": float(values[i])} for i in indices[-4:]]
        return {
            "swing_tags": tags, "high_structure": high_tag, "low_structure": low_tag, "bias": bias,
            "swing_high_count": len(swing_highs), "swing_low_count": len(swing_lows),
            "minor_swings": {"highs": swing_points(swing_highs, highs), "lows": swing_points(swing_lows, lows)},
            "major_swings": {"highs": swing_points(swing_highs[-2:], highs), "lows": swing_points(swing_lows[-2:], lows)},
            "bos_choch": bos_choch, "breakouts": breakouts,
            "liquidity_zones": {"swing_highs": [highs[i] for i in sh[-3:]], "swing_lows": [lows[i] for i in sl[-3:]]},
            "liquidity_sweeps": liquidity_sweeps,
            "fair_value_gaps": fair_value_gaps,
            "order_blocks": order_blocks,
            "displacement_volume_ratio": displacement_volume_ratio,
            "market_regime": {"trend":regime_trend,"volatility":volatility_regime,
                "directional_efficiency":round(efficiency,4),"net_change_pct":round(net_change_pct,6),
                "atr_pct":round(volatility_pct,6)},
            "pivot_confirmation_bars": window,
            "structure_causality": "confirmed_pivots_and_current_completed_candle_only",
            "note": "Swing structure, BOS/CHOCH and close-confirmed/false breakouts are heuristic estimates, not certainty."
        }

    def _candle_patterns(self, df: Any) -> dict[str, Any]:
        try:
            rows = df.rows if hasattr(df, "rows") else list(df)
        except Exception:
            return {"patterns": []}
        if len(rows) < 2:
            return {"patterns": []}
        def candle(row):
            o, h, l, c = map(float, [row["open"], row["high"], row["low"], row["close"]])
            return {"open": o, "high": h, "low": l, "close": c, "body": abs(c-o),
                    "range": max(h-l, 1e-12), "upper_wick": h-max(o,c), "lower_wick": min(o,c)-l}
        prev, cur = candle(rows[-2]), candle(rows[-1])
        patterns: list[str] = []
        bullish = cur["close"] > cur["open"]
        bearish = cur["close"] < cur["open"]
        if bullish: patterns.append("bullish_candle")
        elif bearish: patterns.append("bearish_candle")
        body, rng = cur["body"], cur["range"]
        upper, lower = cur["upper_wick"], cur["lower_wick"]
        if body / rng <= 0.1: patterns.append("doji")
        if prev["close"] < prev["open"] and bullish and cur["close"] >= prev["open"] and cur["open"] <= prev["close"]:
            patterns.append("bullish_engulfing")
        if prev["close"] > prev["open"] and bearish and cur["open"] >= prev["close"] and cur["close"] <= prev["open"]:
            patterns.append("bearish_engulfing")
        prior_closes = [float(r["close"]) for r in rows[-6:-1]]
        prior_trend = "up" if len(prior_closes) >= 2 and prior_closes[-1] > prior_closes[0] else "down" if len(prior_closes) >= 2 and prior_closes[-1] < prior_closes[0] else "sideways"
        lower_pin = lower >= max(body * 2.0, rng * 0.55) and upper <= max(body * 0.6, rng * 0.12)
        upper_pin = upper >= max(body * 2.0, rng * 0.55) and lower <= max(body * 0.6, rng * 0.12)
        if lower_pin:
            patterns.append("pin_bar_bullish")
            patterns.append("hammer" if prior_trend == "down" else "hanging_man" if prior_trend == "up" else "hammer_like")
        if upper_pin:
            patterns.append("pin_bar_bearish")
            patterns.append("shooting_star" if prior_trend == "up" else "shooting_star_like")
        if len(rows) >= 3:
            first = candle(rows[-3])
            middle = candle(rows[-2])
            first_mid = (first["open"] + first["close"]) / 2
            middle_small = middle["body"] / middle["range"] <= 0.35
            if first["close"] < first["open"] and middle_small and bullish and cur["close"] > first_mid:
                patterns.append("morning_star")
            if first["close"] > first["open"] and middle_small and bearish and cur["close"] < first_mid:
                patterns.append("evening_star")
        close_strength = (cur["close"] - cur["low"]) / rng
        return {
            "patterns": patterns, "direction": "bullish" if bullish else "bearish" if bearish else "neutral",
            "body_to_range": round(body / rng, 4), "upper_wick_to_body": round(upper / max(body, 1e-12), 4),
            "lower_wick_to_body": round(lower / max(body, 1e-12), 4),
            "close_strength": round(close_strength, 4), "prior_trend_context": prior_trend,
        }
