import pytest

from app.engines.dynamic_allocation import propose_dynamic_weights


def _rows(value, count=40, regime="trend"):
    return [{"closed": True, "regime": regime, "net_return": value} for _ in range(count)]


def test_dynamic_allocation_falls_back_to_equal_weights_for_small_samples():
    result = propose_dynamic_weights({"a": _rows(0.02, 10), "b": _rows(-0.01, 10)}, regime="trend")
    assert result["ok"] is True
    assert result["status"] == "equal_weight_fallback"
    assert result["weights"] == {"a": 0.5, "b": 0.5}
    assert result["live_capital_changed"] is False


def test_dynamic_allocation_is_regime_aware_and_bounded():
    result = propose_dynamic_weights({
        "winner": _rows(0.03), "loser": _rows(-0.01), "flat": _rows(0.001)
    }, regime="trend", min_samples=30, max_weight=0.6)
    assert result["ok"] is True
    assert sum(result["weights"].values()) == pytest.approx(1.0)
    assert all(0.05 <= weight <= 0.6 for weight in result["weights"].values())
    assert result["weights"]["winner"] > result["weights"]["loser"]
    assert result["live_capital_changed"] is False


def test_dynamic_allocation_ignores_open_trades_and_other_regimes():
    result = propose_dynamic_weights({
        "a": _rows(0.02, 40, "range") + [{"closed": False, "regime": "trend", "net_return": 9}],
        "b": _rows(0.01, 40, "range"),
    }, regime="trend", min_samples=30)
    assert result["status"] == "equal_weight_fallback"


def test_dynamic_allocation_hysteresis_preserves_current_weights():
    outcomes = {"a": _rows(0.02), "b": _rows(0.019)}
    current = {"a": 0.5, "b": 0.5}
    result = propose_dynamic_weights(outcomes, regime="trend", current_weights=current,
                                     rebalance_threshold=0.5)
    assert result["status"] == "hysteresis_hold"
    assert result["weights"] == current


def test_dynamic_allocation_rejects_bad_bounds():
    result = propose_dynamic_weights({"a": _rows(0.01), "b": _rows(0.02)},
                                     regime="trend", min_weight=0.7, max_weight=0.6)
    assert result["ok"] is False


def test_two_strategy_default_bounds_remain_feasible_and_sum_to_one():
    result = propose_dynamic_weights({"a": _rows(0.03), "b": _rows(0.01)}, regime="trend")
    assert result["ok"] is True
    assert sum(result["weights"].values()) == pytest.approx(1.0)
    assert all(0.05 <= value <= 0.6 for value in result["weights"].values())


def test_dynamic_allocation_rejects_hysteresis_weights_outside_concentration_bounds():
    outcomes = {"a": _rows(0.02), "b": _rows(0.01)}
    result = propose_dynamic_weights(
        outcomes, regime="trend", current_weights={"a": 0.9, "b": 0.1},
        rebalance_threshold=1.0, min_weight=0.05, max_weight=0.6,
    )
    assert result == {"ok": False, "error": "invalid_current_weights"}


def test_dynamic_allocation_rejects_extra_current_weight_keys():
    outcomes = {"a": _rows(0.02), "b": _rows(0.01)}
    result = propose_dynamic_weights(
        outcomes, regime="trend", current_weights={"a": 0.5, "b": 0.5, "stale": 0.0},
    )
    assert result == {"ok": False, "error": "invalid_current_weights"}
