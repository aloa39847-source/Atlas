"""Sample-gated, regime-aware research allocation; never changes live capital."""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


def propose_dynamic_weights(
    outcomes: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    regime: str,
    min_samples: int = 30,
    window: int = 100,
    min_weight: float = 0.05,
    max_weight: float = 0.60,
    rebalance_threshold: float = 0.03,
    current_weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Propose bounded weights from closed, fee-adjusted outcomes in the requested regime.

    Uses a conservative positive-expectancy / downside-dispersion score, shrinks
    toward equal weights, and suppresses tiny rebalances. It only returns a proposal.
    """
    if not isinstance(outcomes, Mapping) or len(outcomes) < 2:
        return {"ok": False, "error": "at_least_two_strategy_outcome_sets_required"}
    if isinstance(min_samples, bool) or not isinstance(min_samples, int) or min_samples < 5:
        return {"ok": False, "error": "invalid_min_samples"}
    if isinstance(window, bool) or not isinstance(window, int) or window < min_samples:
        return {"ok": False, "error": "invalid_window"}
    try:
        low, high, threshold = float(min_weight), float(max_weight), float(rebalance_threshold)
    except (TypeError, ValueError, OverflowError):
        return {"ok": False, "error": "invalid_weight_bounds"}
    if not all(math.isfinite(x) for x in (low, high, threshold)) or low <= 0 or high < low or high >= 1 or threshold < 0:
        return {"ok": False, "error": "invalid_weight_bounds"}

    keys = list(outcomes)
    equal = 1.0 / len(keys)
    if low * len(keys) > 1.0 + 1e-9 or high * len(keys) < 1.0 - 1e-9:
        return {"ok": False, "error": "weight_bounds_cannot_sum_to_one"}
    scores: dict[str, float] = {}
    counts: dict[str, int] = {}
    diagnostics: dict[str, dict[str, float]] = {}
    for key, rows in outcomes.items():
        if not isinstance(key, str) or not key or not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            return {"ok": False, "error": "invalid_strategy_outcomes"}
        eligible = []
        for row in rows:
            if not isinstance(row, Mapping) or row.get("closed") is not True or str(row.get("regime", "")) != str(regime):
                continue
            value = row.get("net_return")
            if isinstance(value, bool):
                continue
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(number):
                eligible.append(number)
        sample = eligible[-window:]
        counts[key] = len(sample)
        if len(sample) < min_samples:
            return {"ok": True, "status": "equal_weight_fallback", "weights": {k: equal for k in keys},
                    "sample_counts": counts, "minimum_samples": min_samples,
                    "reason": "insufficient_closed_regime_matched_outcomes",
                    "research_only": True, "live_capital_changed": False}
        mean = sum(sample) / len(sample)
        downside = math.sqrt(sum(min(x, 0.0) ** 2 for x in sample) / len(sample))
        dispersion = math.sqrt(sum((x - mean) ** 2 for x in sample) / len(sample))
        # Positive expectancy only; volatility and downside both penalize concentration.
        score = max(mean, 0.0) / max(dispersion + downside, 1e-9)
        scores[key] = score
        diagnostics[key] = {"expectancy": mean, "volatility": dispersion, "downside_rms": downside}

    if not any(value > 0 for value in scores.values()):
        proposed = {k: equal for k in keys}
    else:
        raw = {k: max(scores[k], 1e-9) for k in keys}
        total = sum(raw.values())
        proposed = {k: 0.5 * equal + 0.5 * raw[k] / total for k in keys}

    # Iterative bounded projection with exact sum-to-one.
    for _ in range(20):
        proposed = {k: min(high, max(low, v)) for k, v in proposed.items()}
        total = sum(proposed.values())
        if math.isclose(total, 1.0, abs_tol=1e-10):
            break
        free = [k for k, v in proposed.items() if low + 1e-10 < v < high - 1e-10]
        if not free:
            break
        adjustment = (1.0 - total) / len(free)
        for k in free:
            proposed[k] += adjustment
    total = sum(proposed.values())
    if not math.isclose(total, 1.0, abs_tol=1e-6):
        proposed = {k: equal for k in keys}

    if current_weights is not None:
        if not isinstance(current_weights, Mapping) or set(current_weights) != set(keys):
            return {"ok": False, "error": "invalid_current_weights"}
        try:
            if any(isinstance(current_weights[k], bool) for k in keys):
                return {"ok": False, "error": "invalid_current_weights"}
            current = {k: float(current_weights[k]) for k in keys}
        except (KeyError, TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "invalid_current_weights"}
        # Hysteresis may hold a prior allocation only when it already satisfies
        # the configured concentration bounds. Otherwise a stale/out-of-policy
        # allocation could bypass the risk limits indefinitely.
        if (any(not math.isfinite(v) or v < low or v > high for v in current.values())
                or not math.isclose(sum(current.values()), 1.0, abs_tol=1e-5)):
            return {"ok": False, "error": "invalid_current_weights"}
        if max(abs(proposed[k] - current[k]) for k in keys) < threshold:
            proposed = current
            status = "hysteresis_hold"
        else:
            status = "proposal_ready"
    else:
        status = "proposal_ready"

    return {"ok": True, "status": status, "weights": proposed,
            "sample_counts": counts, "diagnostics": diagnostics, "scores": scores,
            "regime": str(regime), "window": window, "minimum_samples": min_samples,
            "research_only": True, "live_capital_changed": False}
