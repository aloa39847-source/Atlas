"""Deterministic, offline TITAN market-analysis soak test."""
from __future__ import annotations

import argparse
import gc
import json
import time
import tracemalloc
try:
    import resource
except ImportError:  # pragma: no cover - non-POSIX development hosts
    resource = None
from typing import Callable, Sequence

from app.core.enums import EnvironmentMode, MarketType, Timeframe
from app.engines.decision import DecisionEngine
from app.engines.market_data import MarketDataEngine
from app.engines.technical import TechnicalAnalysisEngine


DEFAULT_SYMBOLS = ("BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT")


def run_soak(*, duration_seconds: int = 1800, symbols: Sequence[str] = DEFAULT_SYMBOLS,
             max_cycles: int | None = None,
             progress_callback: Callable[[dict], None] | None = None,
             progress_interval_seconds: int = 60) -> dict:
    if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, int) or not 1 <= duration_seconds <= 7200:
        raise ValueError("duration_seconds_must_be_between_1_and_7200")
    if (not isinstance(symbols, Sequence) or isinstance(symbols, (str, bytes))
            or not 1 <= len(symbols) <= 60
            or any(not isinstance(symbol, str) or not symbol.strip() for symbol in symbols)
            or len(set(symbols)) != len(symbols)):
        raise ValueError("one_to_sixty_unique_symbols_required")
    if max_cycles is not None and (
        isinstance(max_cycles, bool) or not isinstance(max_cycles, int) or max_cycles < 1
    ):
        raise ValueError("max_cycles_must_be_positive_integer")
    if progress_callback is not None and not callable(progress_callback):
        raise ValueError("progress_callback_must_be_callable")
    if (isinstance(progress_interval_seconds, bool)
            or not isinstance(progress_interval_seconds, int)
            or progress_interval_seconds < 1):
        raise ValueError("progress_interval_seconds_must_be_positive_integer")

    market_types = (MarketType.SPOT, MarketType.FUTURES)
    market_data = MarketDataEngine()
    technical = TechnicalAnalysisEngine()
    decision = DecisionEngine()
    decision.hybrid = None
    decision.multi_agent = None
    cycles = analyses_count = decision_count = 0
    cycle_latency_total = cycle_latency_max = 0.0
    memory_samples = []
    rss_start = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if resource else None
    next_memory_sample = 0.0
    started = time.monotonic()
    last_cycle = started
    next_progress_at = started + progress_interval_seconds
    tracemalloc.start()
    baseline_bytes = tracemalloc.get_traced_memory()[0]
    try:
        while time.monotonic() - started < duration_seconds:
            cycle_started = time.monotonic()
            for market in market_types:
                for index, symbol in enumerate(symbols):
                    batches = market_data.get_multi_timeframe(
                        symbol, limit=250, seed=1000 + cycles * 17 + index,
                        market_type=market.value, timeframes=list(Timeframe),
                    )
                    if (len(batches) != 15 or any(
                        batch.df is None or len(batch.df) == 0
                        or getattr(batch, "market_type", market.value) != market.value
                        for batch in batches.values()
                    )):
                        raise RuntimeError("fifteen_timeframe_market_data_integrity_failure")
                    analyses = {
                        timeframe: technical.analyze(batch.df, timeframe)
                        for timeframe, batch in batches.items()
                    }
                    result = decision.decide(
                        symbol=symbol, market=market, environment=EnvironmentMode.DEMO,
                        timeframe_analyses=analyses, balance=1000.0,
                        max_leverage=1.0 if market == MarketType.SPOT else 3.0,
                        risk_per_trade=0.005,
                    )
                    if result.get("action") not in {"wait", "buy", "sell", "long", "short"}:
                        raise RuntimeError("decision_engine_returned_invalid_action")
                    analyses_count += len(analyses)
                    decision_count += 1
            cycles += 1
            gc.collect()
            last_cycle = time.monotonic()
            cycle_ms = (last_cycle - cycle_started) * 1000.0
            cycle_latency_total += cycle_ms
            cycle_latency_max = max(cycle_latency_max, cycle_ms)
            current_progress_bytes, peak_progress_bytes = tracemalloc.get_traced_memory()
            elapsed_cycle = last_cycle - started
            if elapsed_cycle >= next_memory_sample:
                memory_samples.append({
                    "elapsed_seconds": round(elapsed_cycle, 3),
                    "traced_current_bytes": current_progress_bytes,
                    "traced_peak_bytes": peak_progress_bytes,
                    "process_max_rss_platform_units": (
                        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if resource else None
                    ),
                })
                next_memory_sample = elapsed_cycle + 60.0
            if progress_callback is not None and last_cycle >= next_progress_at:
                progress_callback({
                    "elapsed_seconds": round(elapsed_cycle, 3),
                    "cycles": cycles,
                    "symbols_completed": cycles * len(symbols) * len(market_types),
                    "analysis_count": analyses_count,
                    "decision_count": decision_count,
                    "tracemalloc_current_bytes": current_progress_bytes,
                    "tracemalloc_peak_bytes": peak_progress_bytes,
                })
                next_progress_at = last_cycle + progress_interval_seconds
            if max_cycles is not None and cycles >= max_cycles:
                break
    finally:
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    elapsed = max(0.0, time.monotonic() - started)
    return {
        "ok": True, "mode": "offline_synthetic_soak", "duration_seconds": round(elapsed, 3),
        "requested_duration_seconds": duration_seconds, "cycles": cycles,
        "symbols": list(symbols), "symbol_count": len(symbols), "timeframes_per_symbol": 15,
        "analysis_count": analyses_count, "decision_count": decision_count,
        "market_types": [market.value for market in market_types],
        "decisions_per_cycle": len(symbols) * len(market_types),
        "memory_samples": memory_samples,
        "process_max_rss_start_platform_units": rss_start,
        "process_max_rss_end_platform_units": (
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if resource else None
        ),
        "cycle_latency_avg_ms": round(cycle_latency_total / cycles, 3) if cycles else 0.0,
        "cycle_latency_max_ms": round(cycle_latency_max, 3),
        "tracemalloc_baseline_bytes": baseline_bytes,
        "tracemalloc_current_bytes": current_bytes, "tracemalloc_peak_bytes": peak_bytes,
        "tracemalloc_growth_bytes": current_bytes - baseline_bytes,
        "network_calls": 0, "exchange_orders": 0, "live_trading": False,
        "result": "passed" if cycles > 0 and decision_count == cycles * len(symbols) * len(market_types) else "failed",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=int, default=1800)
    args = parser.parse_args()
    def emit_progress(snapshot: dict) -> None:
        print(json.dumps({"event": "progress", **snapshot}, sort_keys=True), flush=True)

    result = run_soak(
        duration_seconds=args.duration_seconds,
        progress_callback=emit_progress,
        progress_interval_seconds=60,
    )
    print(json.dumps({"event": "final", **result}, sort_keys=True), flush=True)
    if result["result"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
