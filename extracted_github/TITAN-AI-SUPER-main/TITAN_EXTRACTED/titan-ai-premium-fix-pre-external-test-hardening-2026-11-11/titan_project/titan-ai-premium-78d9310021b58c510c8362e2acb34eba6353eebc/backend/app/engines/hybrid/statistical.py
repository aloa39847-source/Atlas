"""Deterministic statistical analysis with finite-input and alignment checks."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from app.core.enums import EngineStatus
from app.engines.base import Engine


def _finite_values(values: Sequence[float]) -> list[float]:
    result = [float(value) for value in values]
    if not all(math.isfinite(value) for value in result):
        raise ValueError("non_finite_statistical_input")
    return result


def _mean(vals: Sequence[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _stdev(vals: Sequence[float]) -> float:
    if len(vals) < 2:
        return 0.0
    mean = _mean(vals)
    variance = sum((value - mean) ** 2 for value in vals) / len(vals)
    return math.sqrt(max(variance, 0.0))


@dataclass
class StatisticalMetrics:
    mean: float
    std: float
    zscore_last: float | None
    correlation: dict[str, float]
    volatility: float
    expectancy: float | None
    win_rate_est: float | None


class StatisticalAnalysisEngine(Engine):
    key = "statistical_analysis"
    name = "Statistical Analysis Engine"

    def status(self):
        return EngineStatus.IMPLEMENTED_PARTIAL

    def analyze_series(self, close: Sequence[float], lookback: int = 20) -> dict[str, Any]:
        if isinstance(lookback, bool) or not isinstance(lookback, int) or not 2 <= lookback <= 10000:
            return {"ok": False, "reason": "lookback_must_be_between_2_and_10000"}
        if not close or len(close) < 2:
            return {"ok": False, "reason": "insufficient_data"}
        try:
            values = _finite_values(close[-lookback:])
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "reason": "invalid_or_non_finite_series"}
        if any(value <= 0 for value in values):
            return {"ok": False, "reason": "prices_must_be_positive"}
        mean = _mean(values)
        std = _stdev(values)
        zscore = (values[-1] - mean) / std if std > 1e-12 else 0.0
        returns = [(values[i] / values[i - 1]) - 1.0 for i in range(1, len(values))]
        volatility = _stdev(returns) if returns else 0.0
        return {
            "ok": True,
            "mean": round(mean, 8),
            "std": round(std, 8),
            "zscore_last": round(zscore, 4),
            "volatility": round(volatility, 8),
            "lookback": len(values),
            "return_samples": len(returns),
            "last_price": values[-1],
            "method": "population_std_for_price_and_return_volatility",
        }

    def correlation_matrix(self, series_map: dict[str, Sequence[float]]) -> dict[str, dict[str, float]]:
        keys = list(series_map)
        aligned: dict[str, list[float]] = {}
        for key in keys:
            try:
                aligned[key] = _finite_values(series_map[key])
            except (TypeError, ValueError, OverflowError):
                aligned[key] = []
        result: dict[str, dict[str, float]] = {}
        for key_a in keys:
            result[key_a] = {}
            for key_b in keys:
                if key_a == key_b:
                    result[key_a][key_b] = 1.0 if len(aligned[key_a]) >= 2 else 0.0
                    continue
                a, b = aligned[key_a], aligned[key_b]
                n = min(len(a), len(b))
                if n < 2:
                    result[key_a][key_b] = 0.0
                    continue
                # Align both series by their most recent observations.
                x, y = a[-n:], b[-n:]
                mean_x, mean_y = _mean(x), _mean(y)
                std_x, std_y = _stdev(x), _stdev(y)
                if std_x < 1e-12 or std_y < 1e-12:
                    correlation = 0.0
                else:
                    covariance = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / n
                    correlation = covariance / (std_x * std_y)
                result[key_a][key_b] = round(max(-1.0, min(1.0, correlation)), 4)
        return result
