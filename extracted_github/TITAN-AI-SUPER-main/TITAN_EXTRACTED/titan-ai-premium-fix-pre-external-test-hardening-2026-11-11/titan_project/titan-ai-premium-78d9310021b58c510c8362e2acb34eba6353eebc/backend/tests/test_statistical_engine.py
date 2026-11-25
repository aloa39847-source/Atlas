from app.engines.hybrid.statistical import StatisticalAnalysisEngine


def test_statistical_engine_returns_finite_series_metrics():
    engine = StatisticalAnalysisEngine()
    result = engine.analyze_series([100, 101, 99, 102, 104, 103], lookback=5)
    assert result["ok"] is True
    assert result["lookback"] == 5
    assert result["return_samples"] == 4
    assert result["std"] >= 0
    assert result["volatility"] >= 0


def test_statistical_engine_rejects_invalid_lookback_and_non_finite_values():
    engine = StatisticalAnalysisEngine()
    assert engine.analyze_series([100, 101], lookback=1)["ok"] is False
    assert engine.analyze_series([100, float("nan"), 101])["reason"] == "invalid_or_non_finite_series"
    assert engine.analyze_series([100, 0, 101])["reason"] == "prices_must_be_positive"


def test_correlation_matrix_uses_aligned_recent_observations_and_is_bounded():
    engine = StatisticalAnalysisEngine()
    result = engine.correlation_matrix({
        "a": [1, 2, 3, 4, 5],
        "b": [10, 20, 30],
        "c": [5, 4, 3, 2, 1],
        "flat": [7, 7, 7],
    })
    assert result["a"]["b"] == 1.0
    assert result["a"]["c"] == -1.0
    assert result["a"]["flat"] == 0.0
    assert all(-1.0 <= value <= 1.0 for row in result.values() for value in row.values())
