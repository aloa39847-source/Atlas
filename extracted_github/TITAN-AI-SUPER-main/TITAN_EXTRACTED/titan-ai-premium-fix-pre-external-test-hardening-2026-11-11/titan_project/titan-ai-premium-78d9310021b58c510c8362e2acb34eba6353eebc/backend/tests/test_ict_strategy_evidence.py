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
                "liquidity_sweeps": [{
                    "type": "sell_side_liquidity_sweep", "direction": "bullish",
                    "level": 100.0, "wick": 99.0, "close": 101.0,
                    "pivot_index": 5, "pivot_confirmed_at": 8,
                    "confirmation_index": 9,
                }],
                "bos_choch": [{"type": "CHOCH", "direction": "bullish", "level": 101.0}],
                "fair_value_gaps": [{"direction": "bullish", "lower": 100.5, "upper": 101.0, "status": "open"}],
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
            "liquidity_sweeps": [{
                "type": "sell_side_liquidity_sweep", "direction": "bullish",
                "level": 100.0, "wick": 99.0, "close": 101.0,
                "pivot_index": 5, "pivot_confirmed_at": 8,
                "confirmation_index": 9,
            }],
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
                "liquidity_sweeps": [{
                    "type": "sell_side_liquidity_sweep", "direction": "bullish",
                    "level": 100.0, "wick": 99.0, "close": 101.0,
                    "pivot_index": 5, "pivot_confirmed_at": 8,
                    "confirmation_index": 9,
                }],
                "bos_choch": [{"type": "CHOCH", "direction": "bullish", "level": 101.0}],
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



def test_ict_sweep_rejects_non_reclaimed_level_and_invalid_pivot_causality():
    htf = {"1d": _frame("bullish"), "4h": _frame("bullish"), "1h": _frame("bullish")}
    invalid_sweeps = [
        {
            "type": "sell_side_liquidity_sweep", "direction": "bullish",
            "level": 100.0, "wick": 99.0, "close": 99.5,
            "pivot_index": 5, "pivot_confirmed_at": 8, "confirmation_index": 9,
        },
        {
            "type": "sell_side_liquidity_sweep", "direction": "bullish",
            "level": 100.0, "wick": 99.0, "close": 101.0,
            "pivot_index": 8, "pivot_confirmed_at": 7, "confirmation_index": 9,
        },
    ]
    for sweep in invalid_sweeps:
        analyses = {
            **htf,
            "15m": _frame("bullish", ["bullish_displacement"], {
                "liquidity_sweeps": [sweep],
                "bos_choch": [{"type": "CHOCH", "direction": "bullish", "level": 101.0}],
                "fair_value_gaps": [{"direction": "bullish", "lower": 100.5, "upper": 101.0, "status": "open"}],
            }),
            "5m": _frame("bullish"),
        }
        candidates = evaluate_advanced_candidates(MarketType.SPOT, "bullish", analyses)
        ict = next(item for item in candidates if item["key"] == "ict_smc_model")
        assert ict["qualified"] is False
        assert "ict_liquidity_raid" not in ict["evidence"]


def test_technical_engine_emits_directional_displacement_from_atr_and_volume():
    from app.engines.market_data import Candles
    from app.engines.technical import TechnicalAnalysisEngine
    from app.core.enums import Timeframe

    rows = []
    for index in range(240):
        close = 100.0 + index * 0.03 + (index % 5) * 0.01
        rows.append({
            "open": close - 0.01,
            "high": close + 0.10,
            "low": close - 0.10,
            "close": close,
            "volume": 100.0,
        })
    last_open = rows[-1]["close"]
    last_close = last_open + 3.0
    rows[-1] = {
        "open": last_open,
        "high": last_close + 0.05,
        "low": last_open - 0.05,
        "close": last_close,
        "volume": 500.0,
    }

    result = TechnicalAnalysisEngine().analyze(Candles(rows), Timeframe.M15)
    assert "bullish_displacement" in result.signals
    assert "bearish_displacement" not in result.signals
