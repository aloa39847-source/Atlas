from types import SimpleNamespace

from app.core.enums import MarketType
from app.engines.advanced_strategies import evaluate_advanced_candidates


def _frame(trend="unknown", signals=(), structure=None):
    return SimpleNamespace(
        trend=trend,
        signals=list(signals),
        indicators={},
        structure=structure or {},
    )


def test_ict_candidate_requires_htf_bias_raid_shift_displacement_and_fvg():
    analyses = {
        "1d": _frame("bullish"),
        "4h": _frame("bullish"),
        "1h": _frame("bullish"),
        "15m": _frame(
            "bullish",
            ["bullish_displacement"],
            {
                "liquidity_sweeps": [{"direction": "bullish", "status": "swept"}],
                "market_structure_shift": {"direction": "bullish", "confirmed": True},
                "fair_value_gaps": [{"direction": "bullish", "status": "open"}],
            },
        ),
        "5m": _frame("bullish"),
    }

    candidates = evaluate_advanced_candidates(MarketType.SPOT, "bullish", analyses)
    ict = next(item for item in candidates if item["key"] == "ict_smc_model")

    assert ict["qualified"] is True
    assert ict["direction"] == "bullish"
    assert {
        "ict_htf_dealing_bias",
        "ict_liquidity_raid",
        "ict_mss_or_choch",
        "ict_displacement",
        "ict_fair_value_gap",
    } <= set(ict["evidence"])


def test_ict_candidate_fails_closed_without_required_structural_confluence():
    analyses = {
        "1d": _frame("bullish"),
        "4h": _frame("bullish"),
        "1h": _frame("bearish"),
        "15m": _frame("bullish", ["bullish_displacement"], {
            "liquidity_sweeps": [{"direction": "bullish", "status": "swept"}],
        }),
        "5m": _frame("bullish"),
    }

    candidates = evaluate_advanced_candidates(MarketType.SPOT, "bullish", analyses)
    ict = next(item for item in candidates if item["key"] == "ict_smc_model")

    assert ict["qualified"] is False
    assert "ict_mss_or_choch" not in ict["evidence"]
    assert "ict_fair_value_gap" not in ict["evidence"]


def test_ict_candidate_does_not_qualify_when_one_core_confluence_is_missing():
    analyses = {
        "1d": _frame("bullish"),
        "4h": _frame("bullish"),
        "1h": _frame("bullish"),
        "15m": _frame(
            "bullish",
            ["bullish_displacement"],
            {
                "liquidity_sweeps": [{"direction": "bullish", "status": "swept"}],
                "market_structure_shift": {"direction": "bullish", "confirmed": True},
                # No open bullish FVG: four evidence points must not pass as a complete ICT setup.
                "fair_value_gaps": [],
            },
        ),
        "5m": _frame("bullish"),
    }
    candidates = evaluate_advanced_candidates(MarketType.SPOT, "bullish", analyses)
    ict = next(item for item in candidates if item["key"] == "ict_smc_model")
    assert ict["score"] == 4
    assert ict["qualified"] is False
