import math

from app.engines.hybrid.ml_engine import MLEngine


def _historical_candles(count=180):
    candles = []
    previous = 100.0
    for i in range(count):
        close = 100.0 + i * 0.08 + math.sin(i * 0.37) * 1.4
        open_price = previous
        high = max(open_price, close) * 1.008
        low = min(open_price, close) * 0.992
        candles.append({
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000.0 + 150.0 * (1.0 + math.sin(i * 0.21)),
            "timestamp": i * 300_000,
        })
        previous = close
    return candles


def test_ml_engine_trains_chronologically_and_exports_explainable_predictions():
    engine = MLEngine()
    candles = _historical_candles()
    trained = engine.train(candles, train_ratio=0.8, epochs=100)

    assert trained["ok"] is True
    assert trained["model"]["training_samples"] > trained["model"]["test_samples"]
    assert trained["model"]["metrics"]["baseline_accuracy"] >= 0.0
    assert 0.0 <= trained["model"]["metrics"]["brier_score"] <= 1.0
    assert 0.0 <= trained["model"]["metrics"]["expected_calibration_error"] <= 1.0
    assert trained["model"]["metrics"]["calibration_bins"]

    prediction = engine.predict(candles[-6:])
    assert prediction["ok"] is True
    assert 0.0 <= prediction["prob_up"] <= 1.0
    assert 0.0 <= prediction["prob_down"] <= 1.0
    assert prediction["execution"] == "none"
    assert set(prediction["feature_contributions"]) == {
        "return_1", "return_3", "return_5", "range_pct", "body_pct",
        "upper_wick_pct", "lower_wick_pct", "volume_ratio", "volatility_5",
    }

    restored = MLEngine()
    assert restored.load_model(engine.export_model())["ok"] is True
    assert restored.predict(candles[-6:])["prob_up"] == prediction["prob_up"]


def test_ml_engine_rejects_insufficient_and_non_finite_training_data():
    engine = MLEngine()
    assert engine.train(_historical_candles(30))["error"] == "at_least_50_historical_candles_required"

    bad = _historical_candles()
    bad[20]["close"] = float("nan")
    result = engine.train(bad)
    assert result["ok"] is False
    assert result["error"] == "invalid_training_candles"


def test_ml_engine_does_not_claim_a_trained_probability_without_a_model():
    engine = MLEngine()
    assert engine.predict(_historical_candles()[-6:])["error"] == "trained_model_required"
    heuristic = engine.probability_estimate({"trend_bull": True})
    assert heuristic["model"] == "heuristic_not_trained"


def test_ml_engine_rejects_boolean_candle_values_and_training_hyperparameters():
    engine = MLEngine()
    bad_candles = _historical_candles()
    bad_candles[10]["volume"] = True
    assert engine.train(bad_candles)["error"] == "invalid_training_candles"
    assert engine.train(_historical_candles(), l2=True)["error"] == "boolean_training_configuration_not_allowed"
    assert engine.train(_historical_candles(), learning_rate="bad")["error"] == "invalid_training_configuration"
    assert engine.train(_historical_candles(), model_key="../unsafe")["error"] == "invalid_model_key"


def test_ml_engine_evaluates_calibration_and_flags_drift_without_retraining():
    engine = MLEngine()
    candles = _historical_candles()
    trained = engine.train(candles, train_ratio=0.8, epochs=100)
    assert trained["ok"] is True
    model_before = engine.export_model()
    engine.model["metrics"]["accuracy"] = 1.0
    engine.model["metrics"]["brier_score"] = 0.0

    later_candles = _historical_candles()
    offset = candles[-1]["timestamp"]
    for index, candle in enumerate(later_candles, start=1):
        candle["timestamp"] = offset + index * 300_000
    result = engine.evaluate_drift(later_candles)
    assert result["ok"] is True
    assert result["samples"] >= 20
    assert result["drift_detected"] is True
    assert result["recent"]["calibration_bins"]
    assert result["model_changed"] is False
    assert result["automatic_retraining"] is False
    assert engine.export_model()["weights"] == model_before["weights"]


def test_ml_engine_drift_evaluation_fails_closed_on_invalid_inputs():
    engine = MLEngine()
    assert engine.evaluate_drift(_historical_candles())["error"] == "trained_model_required"
    engine.train(_historical_candles(), epochs=50)
    assert engine.evaluate_drift(_historical_candles(), brier_increase_threshold=True)["error"] == "boolean_drift_threshold_not_allowed"
    assert engine.evaluate_drift(_historical_candles(20))["error"] == "at_least_30_recent_candles_required"
    same_window = _historical_candles()
    for index, candle in enumerate(same_window):
        candle["timestamp"] = max(1, index * 300_000)
    assert engine.evaluate_drift(same_window)["error"] == "at_least_20_labeled_recent_samples_required"


def test_ml_training_purges_labels_that_cross_into_the_test_window():
    engine = MLEngine()
    trained = engine.train(_historical_candles(), lookahead=5, train_ratio=0.8, epochs=50)
    assert trained["ok"] is True
    model = trained["model"]
    assert model["train_label_end_index"] < model["test_start_index"]
    assert model["purged_boundary_samples"] >= 5
    assert "purged_label_boundary" in model["training_scope"]
