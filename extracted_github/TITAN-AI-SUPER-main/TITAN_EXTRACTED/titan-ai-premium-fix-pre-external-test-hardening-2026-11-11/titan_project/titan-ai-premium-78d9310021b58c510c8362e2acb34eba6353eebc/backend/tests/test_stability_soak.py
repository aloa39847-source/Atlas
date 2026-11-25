from app.scripts.run_stability_soak import run_soak

# Final audit trigger: exercise the current 15-timeframe source head.


def test_offline_stability_soak_runs_all_fifteen_timeframes_without_network_or_orders():
    result = run_soak(duration_seconds=5, symbols=("BTC/USDT",), max_cycles=1)
    assert result["ok"] is True
    assert result["cycles"] == 1
    assert result["timeframes_per_symbol"] == 15
    assert result["analysis_count"] == 30
    assert result["decision_count"] == 2
    assert result["market_types"] == ["spot", "futures"]
    assert result["decisions_per_cycle"] == 2
    assert result["memory_samples"]
    assert result["process_max_rss_end_platform_units"] is None or result["process_max_rss_end_platform_units"] >= result["process_max_rss_start_platform_units"]
    assert result["network_calls"] == 0
    assert result["exchange_orders"] == 0
    assert result["live_trading"] is False
    assert result["result"] == "passed"
    assert result["symbol_count"] == 1
    assert result["cycle_latency_max_ms"] >= 0
    assert result["tracemalloc_peak_bytes"] >= result["tracemalloc_baseline_bytes"]


def test_offline_soak_rejects_more_than_sixty_or_duplicate_symbols():
    import pytest
    with pytest.raises(ValueError, match="one_to_sixty_unique_symbols_required"):
        run_soak(duration_seconds=1, symbols=tuple(f"S{i}USDT" for i in range(61)), max_cycles=1)
    with pytest.raises(ValueError, match="one_to_sixty_unique_symbols_required"):
        run_soak(duration_seconds=1, symbols=("BTC/USDT", "BTC/USDT"), max_cycles=1)


def test_offline_soak_rejects_invalid_cycle_limit():
    import pytest
    with pytest.raises(ValueError, match="max_cycles_must_be_positive_integer"):
        run_soak(duration_seconds=1, symbols=("BTC/USDT",), max_cycles=True)


def test_offline_soak_emits_progress_snapshot_when_interval_is_reached():
    snapshots = []

    result = run_soak(
        duration_seconds=1,
        symbols=("BTC/USDT",),
        progress_callback=snapshots.append,
        progress_interval_seconds=1,
    )

    assert result["result"] == "passed"
    assert snapshots
    assert snapshots[0]["cycles"] >= 1
    assert snapshots[0]["decision_count"] == snapshots[0]["cycles"] * 2
    assert snapshots[0]["analysis_count"] == snapshots[0]["cycles"] * 30


def test_offline_soak_rejects_invalid_progress_configuration():
    import pytest

    with pytest.raises(ValueError, match="progress_callback_must_be_callable"):
        run_soak(duration_seconds=1, symbols=("BTC/USDT",), max_cycles=1,
                 progress_callback="not-callable")
    with pytest.raises(ValueError, match="progress_interval_seconds_must_be_positive_integer"):
        run_soak(duration_seconds=1, symbols=("BTC/USDT",), max_cycles=1,
                 progress_interval_seconds=True)


def test_offline_soak_rejects_empty_candle_batch_before_analysis(monkeypatch):
    import pytest
    from app.engines.market_data import Candles, MarketDataEngine

    original = MarketDataEngine.get_multi_timeframe

    def with_empty_batch(self, *args, **kwargs):
        batches = original(self, *args, **kwargs)
        timeframe = next(iter(batches))
        batch = batches[timeframe]
        batches[timeframe] = type(batch)(
            symbol=batch.symbol, timeframe=batch.timeframe, df=Candles([]),
            source=batch.source, note=batch.note, market_type=batch.market_type,
        )
        return batches

    monkeypatch.setattr(MarketDataEngine, "get_multi_timeframe", with_empty_batch)
    with pytest.raises(RuntimeError, match="fifteen_timeframe_market_data_integrity_failure"):
        run_soak(duration_seconds=1, symbols=("BTC/USDT",), max_cycles=1)
