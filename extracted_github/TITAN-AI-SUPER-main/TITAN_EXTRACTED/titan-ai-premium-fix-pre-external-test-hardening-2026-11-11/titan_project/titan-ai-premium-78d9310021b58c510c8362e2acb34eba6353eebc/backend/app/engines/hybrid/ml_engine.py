"""Explainable, trainable ML baseline for historical candle features.

The model is an offline research component. It requires labeled historical
candles, uses chronological train/test separation, and never executes trades.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Sequence

from app.core.enums import EngineStatus
from app.engines.base import Engine


FEATURE_NAMES = (
    "return_1", "return_3", "return_5", "range_pct", "body_pct",
    "upper_wick_pct", "lower_wick_pct", "volume_ratio", "volatility_5",
)


class MLEngine(Engine):
    key = "ml_engine"
    name = "Machine Learning Pattern Engine"

    def __init__(self) -> None:
        self.model: dict[str, Any] | None = None
        self.models: dict[str, dict[str, Any]] = {}

    def status(self):
        return EngineStatus.IMPLEMENTED_PARTIAL

    @staticmethod
    def _finite(value: Any) -> float:
        if isinstance(value, bool):
            raise ValueError("boolean_candle_value_not_allowed")
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("non_finite_candle_value")
        return result

    def _feature_row(self, candles: Sequence[dict], index: int) -> list[float]:
        current = candles[index]
        opens = [self._finite(candles[i]["open"]) for i in range(index - 5, index + 1)]
        highs = [self._finite(candles[i]["high"]) for i in range(index - 5, index + 1)]
        lows = [self._finite(candles[i]["low"]) for i in range(index - 5, index + 1)]
        closes = [self._finite(candles[i]["close"]) for i in range(index - 5, index + 1)]
        volumes = [self._finite(candles[i].get("volume", 0.0)) for i in range(index - 5, index + 1)]
        if any(value <= 0 for value in opens + highs + lows + closes) or any(value < 0 for value in volumes):
            raise ValueError("invalid_ohlcv_value")
        open_price, high, low, close = opens[-1], highs[-1], lows[-1], closes[-1]
        if high < max(open_price, close, low) or low > min(open_price, close, high):
            raise ValueError("invalid_ohlc_geometry")
        mean_volume = sum(volumes[:-1]) / max(1, len(volumes) - 1)
        returns = [(closes[i] / closes[i - 1]) - 1.0 for i in range(1, len(closes))]
        mean_return = sum(returns) / len(returns)
        volatility = math.sqrt(sum((x - mean_return) ** 2 for x in returns) / len(returns))
        return [
            close / closes[-2] - 1.0,
            close / closes[-4] - 1.0,
            close / closes[-6] - 1.0,
            (high - low) / close,
            (close - open_price) / open_price,
            (high - max(open_price, close)) / close,
            (min(open_price, close) - low) / close,
            (volumes[-1] / mean_volume - 1.0) if mean_volume > 1e-12 else 0.0,
            volatility,
        ]

    @staticmethod
    def _sigmoid(value: float) -> float:
        value = max(-35.0, min(35.0, value))
        return 1.0 / (1.0 + math.exp(-value))

    def train(
        self,
        candles: Sequence[dict],
        *,
        lookahead: int = 1,
        train_ratio: float = 0.8,
        learning_rate: float = 0.05,
        epochs: int = 400,
        l2: float = 0.001,
        model_key: str = "default",
    ) -> dict[str, Any]:
        if not isinstance(candles, Sequence) or isinstance(candles, (str, bytes)) or len(candles) < 50:
            return {"ok": False, "error": "at_least_50_historical_candles_required"}
        if isinstance(lookahead, bool) or not isinstance(lookahead, int) or not 1 <= lookahead <= 10:
            return {"ok": False, "error": "lookahead_must_be_between_1_and_10"}
        if isinstance(epochs, bool) or not isinstance(epochs, int) or not 10 <= epochs <= 5000:
            return {"ok": False, "error": "epochs_must_be_between_10_and_5000"}
        if any(isinstance(value, bool) for value in (train_ratio, learning_rate, l2)):
            return {"ok": False, "error": "boolean_training_configuration_not_allowed"}
        try:
            train_ratio = float(train_ratio)
            learning_rate = float(learning_rate)
            l2 = float(l2)
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "invalid_training_configuration"}
        if not math.isfinite(train_ratio) or not 0.6 <= train_ratio <= 0.9:
            return {"ok": False, "error": "train_ratio_must_be_between_0_6_and_0_9"}
        if not math.isfinite(learning_rate) or not 0 < learning_rate <= 0.5:
            return {"ok": False, "error": "learning_rate_out_of_range"}
        if not math.isfinite(l2) or not 0 <= l2 <= 1:
            return {"ok": False, "error": "l2_out_of_range"}
        if (not isinstance(model_key, str)
                or not re.fullmatch(r"[A-Za-z0-9_./|:-]{1,100}", model_key)
                or ".." in model_key or model_key.startswith("/")):
            return {"ok": False, "error": "invalid_model_key"}

        try:
            closes = [self._finite(candle["close"]) for candle in candles]
            if any(value <= 0 for value in closes):
                raise ValueError("close_prices_must_be_positive")
            features: list[list[float]] = []
            labels: list[int] = []
            sample_indices: list[int] = []
            for index in range(5, len(candles) - lookahead):
                features.append(self._feature_row(candles, index))
                labels.append(1 if closes[index + lookahead] > closes[index] else 0)
                sample_indices.append(index)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            return {"ok": False, "error": "invalid_training_candles", "detail": str(exc)[:160]}

        split = int(len(features) * float(train_ratio))
        if split >= len(sample_indices):
            return {"ok": False, "error": "insufficient_train_or_test_samples"}
        # Purge training rows whose future label reaches the first test feature
        # timestamp. Test features may use earlier candles as history, but no
        # training label may consume information from the held-out test window.
        test_start_index = sample_indices[split]
        train_count = sum(
            1 for index in sample_indices[:split]
            if index + lookahead < test_start_index
        )
        x_train, x_test = features[:train_count], features[split:]
        y_train, y_test = labels[:train_count], labels[split:]
        if len(x_train) < 20 or len(x_test) < 5:
            return {"ok": False, "error": "insufficient_train_or_test_samples",
                    "train_samples": len(x_train), "test_samples": len(x_test)}

        means = [sum(row[j] for row in x_train) / len(x_train) for j in range(len(FEATURE_NAMES))]
        scales = []
        for j, mean in enumerate(means):
            variance = sum((row[j] - mean) ** 2 for row in x_train) / len(x_train)
            scales.append(math.sqrt(variance) if variance > 1e-18 else 1.0)

        def normalize(row: Sequence[float]) -> list[float]:
            return [(row[j] - means[j]) / scales[j] for j in range(len(FEATURE_NAMES))]

        normalized_train = [normalize(row) for row in x_train]
        normalized_test = [normalize(row) for row in x_test]
        weights = [0.0] * (len(FEATURE_NAMES) + 1)
        for _ in range(epochs):
            gradients = [0.0] * len(weights)
            for row, label in zip(normalized_train, y_train):
                probability = self._sigmoid(weights[0] + sum(weights[j + 1] * row[j] for j in range(len(row))))
                error = probability - label
                gradients[0] += error
                for j, value in enumerate(row):
                    gradients[j + 1] += error * value
            count = float(len(normalized_train))
            weights[0] -= float(learning_rate) * gradients[0] / count
            for j in range(1, len(weights)):
                gradient = gradients[j] / count + float(l2) * weights[j]
                weights[j] -= float(learning_rate) * gradient

        probabilities = [
            self._sigmoid(weights[0] + sum(weights[j + 1] * row[j] for j in range(len(row))))
            for row in normalized_test
        ]
        predictions = [1 if probability >= 0.5 else 0 for probability in probabilities]
        tp = sum(1 for p, y in zip(predictions, y_test) if p == 1 and y == 1)
        tn = sum(1 for p, y in zip(predictions, y_test) if p == 0 and y == 0)
        fp = sum(1 for p, y in zip(predictions, y_test) if p == 1 and y == 0)
        fn = sum(1 for p, y in zip(predictions, y_test) if p == 0 and y == 1)
        accuracy = (tp + tn) / len(y_test)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        brier = sum((p - y) ** 2 for p, y in zip(probabilities, y_test)) / len(y_test)
        calibration = self._calibration_report(probabilities, y_test)
        baseline = max(sum(y_test), len(y_test) - sum(y_test)) / len(y_test)
        raw_training_end = candles[-1].get("close_time") or candles[-1].get("timestamp")
        training_end_timestamp = None
        if raw_training_end is not None and not isinstance(raw_training_end, bool):
            try:
                candidate_stamp = float(raw_training_end)
                if math.isfinite(candidate_stamp) and candidate_stamp > 0 and candidate_stamp.is_integer():
                    training_end_timestamp = int(candidate_stamp)
            except (TypeError, ValueError, OverflowError):
                training_end_timestamp = None

        self.model = {
            "model_key": str(model_key or "default"),
            "model_version": "explainable_logistic_candles_v1",
            "feature_names": list(FEATURE_NAMES),
            "means": means,
            "scales": scales,
            "weights": weights,
            "lookahead": lookahead,
            "train_ratio": float(train_ratio),
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "training_end_timestamp": training_end_timestamp,
            "training_samples": len(x_train),
            "test_samples": len(x_test),
            "train_label_end_index": sample_indices[train_count - 1] + lookahead,
            "test_start_index": test_start_index,
            "purged_boundary_samples": split - train_count,
            "metrics": {
                "accuracy": round(accuracy, 4),
                "precision_up": round(precision, 4),
                "recall_up": round(recall, 4),
                "brier_score": round(brier, 6),
                "expected_calibration_error": calibration["expected_calibration_error"],
                "calibration_bins": calibration["bins"],
                "baseline_accuracy": round(baseline, 4),
                "true_positive": tp, "true_negative": tn,
                "false_positive": fp, "false_negative": fn,
            },
            "training_scope": "chronological_split_with_purged_label_boundary; normalization fitted on training data only",
        }
        self.models[self.model["model_key"]] = self.model
        return {"ok": True, "model": self.export_model(self.model["model_key"]), "metrics": self.model["metrics"],
                "note": "Research baseline only. Out-of-sample metrics are historical and do not imply future profitability."}

    @staticmethod
    def _calibration_report(probabilities: Sequence[float], labels: Sequence[int], bins: int = 10) -> dict[str, Any]:
        if len(probabilities) != len(labels) or not probabilities:
            return {"expected_calibration_error": None, "bins": []}
        buckets = [{"count": 0, "probability_sum": 0.0, "positive_sum": 0} for _ in range(bins)]
        for probability, label in zip(probabilities, labels):
            index = min(bins - 1, int(float(probability) * bins))
            bucket = buckets[index]
            bucket["count"] += 1
            bucket["probability_sum"] += float(probability)
            bucket["positive_sum"] += int(label)
        report = []
        total = len(probabilities)
        ece = 0.0
        for index, bucket in enumerate(buckets):
            count = bucket["count"]
            if not count:
                continue
            mean_probability = bucket["probability_sum"] / count
            observed_frequency = bucket["positive_sum"] / count
            gap = abs(mean_probability - observed_frequency)
            ece += (count / total) * gap
            report.append({
                "lower_bound": round(index / bins, 2),
                "upper_bound": round((index + 1) / bins, 2),
                "count": count,
                "mean_probability": round(mean_probability, 6),
                "observed_positive_rate": round(observed_frequency, 6),
                "absolute_gap": round(gap, 6),
            })
        return {"expected_calibration_error": round(ece, 6), "bins": report}

    def evaluate_drift(
        self,
        candles: Sequence[dict],
        *,
        model_key: str | None = None,
        brier_increase_threshold: float = 0.05,
        accuracy_drop_threshold: float = 0.10,
    ) -> dict[str, Any]:
        """Evaluate a trained model on a later labeled window; never retrains it."""
        model = self.models.get(model_key) if model_key is not None else self.model
        if model is None:
            return {"ok": False, "error": "trained_model_required"}
        if not isinstance(candles, Sequence) or isinstance(candles, (str, bytes)) or len(candles) < 30:
            return {"ok": False, "error": "at_least_30_recent_candles_required"}
        if any(isinstance(value, bool) for value in (brier_increase_threshold, accuracy_drop_threshold)):
            return {"ok": False, "error": "boolean_drift_threshold_not_allowed"}
        try:
            brier_threshold = float(brier_increase_threshold)
            accuracy_threshold = float(accuracy_drop_threshold)
            lookahead = model["lookahead"]
            if isinstance(lookahead, bool) or not isinstance(lookahead, int) or not 1 <= lookahead <= 10:
                raise ValueError("invalid_model_lookahead")
            if (not math.isfinite(brier_threshold) or brier_threshold < 0
                    or not math.isfinite(accuracy_threshold) or accuracy_threshold < 0):
                raise ValueError("invalid_drift_threshold")
            closes = [self._finite(row["close"]) for row in candles]
            if any(value <= 0 for value in closes):
                raise ValueError("close_prices_must_be_positive")
            raw_training_end = model.get("training_end_timestamp")
            if isinstance(raw_training_end, bool) or raw_training_end is None:
                raise ValueError("model_training_end_timestamp_missing")
            training_end = int(raw_training_end)
            timestamps = []
            for row in candles:
                raw_stamp = row.get("close_time") or row.get("timestamp")
                if isinstance(raw_stamp, bool) or raw_stamp is None:
                    raise ValueError("recent_candle_timestamp_required")
                stamp = float(raw_stamp)
                if not math.isfinite(stamp) or stamp <= 0 or not stamp.is_integer():
                    raise ValueError("invalid_recent_candle_timestamp")
                timestamps.append(int(stamp))
            if any(timestamps[index] <= timestamps[index - 1] for index in range(1, len(timestamps))):
                raise ValueError("recent_candle_timestamps_must_increase")
            probabilities = []
            labels = []
            for index in range(5, len(candles) - lookahead):
                if timestamps[index] <= training_end:
                    continue
                features = self._feature_row(candles, index)
                normalized = [
                    (features[j] - model["means"][j]) / model["scales"][j]
                    for j in range(len(FEATURE_NAMES))
                ]
                weights = model["weights"]
                probability = self._sigmoid(
                    weights[0] + sum(weights[j + 1] * normalized[j] for j in range(len(FEATURE_NAMES)))
                )
                probabilities.append(probability)
                labels.append(1 if closes[index + lookahead] > closes[index] else 0)
        except (KeyError, TypeError, ValueError, OverflowError, IndexError, AttributeError) as exc:
            return {"ok": False, "error": "invalid_drift_evaluation_data", "detail": str(exc)[:160]}
        if len(labels) < 20:
            return {"ok": False, "error": "at_least_20_labeled_recent_samples_required"}
        predictions = [1 if probability >= 0.5 else 0 for probability in probabilities]
        accuracy = sum(prediction == label for prediction, label in zip(predictions, labels)) / len(labels)
        brier = sum((probability - label) ** 2 for probability, label in zip(probabilities, labels)) / len(labels)
        calibration = self._calibration_report(probabilities, labels)
        baseline_metrics = model.get("metrics") or {}
        try:
            baseline_accuracy = float(baseline_metrics["accuracy"])
            baseline_brier = float(baseline_metrics["brier_score"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "model_baseline_metrics_missing"}
        if not all(math.isfinite(value) for value in (baseline_accuracy, baseline_brier)):
            return {"ok": False, "error": "model_baseline_metrics_invalid"}
        brier_delta = brier - baseline_brier
        accuracy_delta = accuracy - baseline_accuracy
        drift_detected = (
            brier_delta > brier_threshold or accuracy_delta < -accuracy_threshold
        )
        return {
            "ok": True,
            "model_key": model.get("model_key"),
            "model_version": model.get("model_version"),
            "samples": len(labels),
            "baseline": {"accuracy": round(baseline_accuracy, 6), "brier_score": round(baseline_brier, 6)},
            "recent": {
                "accuracy": round(accuracy, 6),
                "brier_score": round(brier, 6),
                "expected_calibration_error": calibration["expected_calibration_error"],
                "calibration_bins": calibration["bins"],
            },
            "deltas": {"accuracy": round(accuracy_delta, 6), "brier_score": round(brier_delta, 6)},
            "drift_detected": drift_detected,
            "status": "drift_detected" if drift_detected else "within_configured_thresholds",
            "model_changed": False,
            "automatic_retraining": False,
            "note": "Supply a strictly later labeled out-of-sample window. Drift is a review alert, not an automatic model update or trading signal.",
        }

    def predict(self, candles: Sequence[dict], *, model_key: str | None = None) -> dict[str, Any]:
        model = self.models.get(model_key) if model_key is not None else self.model
        if model is None:
            return {"ok": False, "error": "trained_model_required"}
        if len(candles) < 6:
            return {"ok": False, "error": "six_candles_required_for_prediction"}
        try:
            row = self._feature_row(candles, len(candles) - 1)
            normalized = [
                (row[j] - model["means"][j]) / model["scales"][j]
                for j in range(len(FEATURE_NAMES))
            ]
            weights = model["weights"]
            probability = self._sigmoid(weights[0] + sum(weights[j + 1] * normalized[j] for j in range(len(row))))
        except (KeyError, TypeError, ValueError, OverflowError, IndexError) as exc:
            return {"ok": False, "error": "invalid_prediction_candles", "detail": str(exc)[:160]}
        contributions = {
            FEATURE_NAMES[j]: round(weights[j + 1] * normalized[j], 6)
            for j in range(len(FEATURE_NAMES))
        }
        return {
            "ok": True,
            "prob_up": round(probability, 4),
            "prob_down": round(1.0 - probability, 4),
            "predicted_direction": "up" if probability >= 0.5 else "down",
            "confidence": round(max(probability, 1.0 - probability), 4),
            "feature_contributions": contributions,
            "model_version": model["model_version"],
            "trained_at": model["trained_at"],
            "execution": "none",
            "note": "Model output is an estimate from a small historical baseline, not a calibrated probability or trade instruction.",
        }

    def export_model(self, model_key: str | None = None) -> dict[str, Any] | None:
        model = self.models.get(model_key) if model_key is not None else self.model
        if model is None:
            return None
        return {
            **model,
            "means": list(model["means"]),
            "scales": list(model["scales"]),
            "weights": list(model["weights"]),
            "feature_names": list(model["feature_names"]),
            "metrics": dict(model["metrics"]),
        }

    def load_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            if payload.get("model_version") != "explainable_logistic_candles_v1":
                raise ValueError("unsupported_model_version")
            if payload.get("feature_names") != list(FEATURE_NAMES):
                raise ValueError("feature_schema_mismatch")
            means = [float(x) for x in payload["means"]]
            scales = [float(x) for x in payload["scales"]]
            weights = [float(x) for x in payload["weights"]]
            if len(means) != len(FEATURE_NAMES) or len(scales) != len(FEATURE_NAMES) or len(weights) != len(FEATURE_NAMES) + 1:
                raise ValueError("invalid_model_dimensions")
            if not all(math.isfinite(x) for x in means + scales + weights) or any(x <= 0 for x in scales):
                raise ValueError("invalid_model_numeric_values")
            key = str(payload.get("model_key") or "default")
            raw_training_end = payload.get("training_end_timestamp")
            if raw_training_end is not None:
                if isinstance(raw_training_end, bool):
                    raise ValueError("invalid_training_end_timestamp")
                training_end = float(raw_training_end)
                if not math.isfinite(training_end) or training_end <= 0 or not training_end.is_integer():
                    raise ValueError("invalid_training_end_timestamp")
                raw_training_end = int(training_end)
            self.model = {
                **payload, "model_key": key, "means": means, "scales": scales, "weights": weights,
                "feature_names": list(FEATURE_NAMES), "training_end_timestamp": raw_training_end,
            }
            self.models[key] = self.model
            return {"ok": True, "model_version": self.model["model_version"], "model_key": key}
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            return {"ok": False, "error": "invalid_model_payload", "detail": str(exc)[:160]}

    def detect_patterns(self, candles: Sequence[dict]) -> dict[str, Any]:
        if not candles or len(candles) < 5:
            return {"patterns": [], "confidence": 0.0, "explainable": True}
        try:
            rows = [
                {
                    "high": self._finite(row["high"]),
                    "low": self._finite(row["low"]),
                    "volume": self._finite(row.get("volume", 0.0)),
                }
                for row in candles
            ]
        except (KeyError, TypeError, ValueError, OverflowError):
            return {"patterns": [], "confidence": 0.0, "error": "invalid_pattern_candles", "explainable": True}
        patterns = []
        if len(rows) >= 3:
            if rows[-1]["high"] > rows[-2]["high"] > rows[-3]["high"]:
                patterns.append({"name": "ascending_sequence", "type": "structure", "confidence": 0.6})
            if rows[-1]["low"] > rows[-2]["low"] > rows[-3]["low"]:
                patterns.append({"name": "higher_lows", "type": "structure", "confidence": 0.6})
        vols = [row["volume"] for row in rows[-10:]]
        if len(vols) > 1 and vols[-1] > sum(vols[:-1]) / len(vols[:-1]):
            patterns.append({"name": "volume_spike", "type": "volume", "confidence": 0.55})
        confidence = sum(p["confidence"] for p in patterns) / len(patterns) if patterns else 0.0
        return {"patterns": patterns, "confidence": round(confidence, 3), "explainable": True}

    def probability_estimate(self, features: dict[str, Any]) -> dict[str, Any]:
        """Transparent heuristic fallback; not a trained model prediction."""
        score = 0.0
        weights = []
        if features.get("trend_bull"):
            score += 0.2
            weights.append("trend_bull:+0.2")
        if features.get("rsi_ok"):
            score += 0.1
            weights.append("rsi_ok:+0.1")
        if features.get("volume_ok"):
            score += 0.15
            weights.append("volume_ok:+0.15")
        if features.get("structure_bull"):
            score += 0.2
            weights.append("structure_bull:+0.2")
        probability = min(0.95, max(0.05, score))
        return {
            "prob_up": round(probability, 3),
            "prob_down": round(1 - probability, 3),
            "weights": weights,
            "explainable": True,
            "model": "heuristic_not_trained",
        }


# Shared process-level model registry used by the hybrid coordinator and API.
ml_engine = MLEngine()
