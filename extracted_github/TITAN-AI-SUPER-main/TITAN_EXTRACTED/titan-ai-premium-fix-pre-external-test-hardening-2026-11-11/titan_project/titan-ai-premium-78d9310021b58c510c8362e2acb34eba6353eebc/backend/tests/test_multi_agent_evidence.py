from app.agents.coordinator import MultiAgentCoordinator
from app.agents.specialists import (
    TechnicalAgent, LiquidityAgent, RiskAgent, NewsAgent, StrategyAgent, StrategyMemoryAgent,
)


def test_technical_agent_uses_supplied_timeframe_evidence():
    context = {
        "bias": "neutral",
        "timeframe_analyses": {
            "1d": {"trend": "bullish", "signals": ["ema_alignment"]},
            "4h": {"trend": "bullish", "signals": ["higher_high"]},
            "1h": {"trend": "bullish", "signals": ["bos_up"]},
            "15m": {"trend": "bearish", "signals": ["pullback"]},
            "5m": {"trend": "bullish", "signals": ["reclaim"]},
        },
    }
    vote = TechnicalAgent().analyze(context)
    assert vote.bias == "bullish"
    assert vote.confidence > 0.5
    assert any("1d_trend:bullish" == item for item in vote.evidence)
    assert any("4h_signal:higher_high" == item for item in vote.evidence)


def test_liquidity_agent_uses_only_verified_metrics_and_handles_bad_values():
    verified = LiquidityAgent().analyze({
        "microstructure_analysis": {
            "liquidity": {"ok": True, "depth_imbalance": 0.55},
            "order_flow": {"ok": True, "taker_flow_imbalance": 0.35},
        },
    })
    assert verified.bias == "bullish"
    assert any("verified_depth_imbalance:0.5500" == item for item in verified.evidence)

    invalid = LiquidityAgent().analyze({
        "microstructure_analysis": {
            "liquidity": {"ok": True, "depth_imbalance": "not-a-number"},
            "order_flow": {"ok": False},
        },
    })
    assert invalid.bias == "neutral"
    assert "verified_order_book_metric_invalid" in invalid.risk_notes


def test_risk_agent_reflects_deterministic_risk_veto():
    vote = RiskAgent().analyze({
        "risk_result": {"ok": False, "issues": ["daily_loss_limit_reached"]},
    })
    assert vote.veto is True
    assert "daily_loss_limit_reached" in vote.risk_notes


def test_news_agent_ignores_unverified_and_stale_news():
    vote = NewsAgent().analyze({
        "news_context": {"items": [
            {"source_type": "community", "freshness": "fresh",
             "sentiment_score": 1.0, "sentiment_confidence": 1.0},
            {"source_type": "official_primary", "freshness": "stale",
             "sentiment_score": -1.0, "sentiment_confidence": 1.0},
        ]},
    })
    assert vote.bias == "neutral"
    assert vote.confidence <= 0.1
    assert vote.evidence == ["no_fresh_verified_official_news"]


def test_strategy_agent_reports_missing_candidate_without_forcing_a_strategy():
    vote = StrategyAgent().analyze({"strategy": {"key": "none"}, "bias": "bullish"})
    assert vote.bias == "neutral"
    assert vote.confidence == 0.0
    assert "do_not_force_a_strategy" in vote.risk_notes


def test_coordinator_passes_risk_veto_and_has_no_order_authority():
    result = MultiAgentCoordinator().deliberate({
        "bias": "bullish",
        "timeframe_analyses": {
            "1d": {"trend": "bullish", "signals": ["trend_up"]},
            "4h": {"trend": "bullish", "signals": ["trend_up"]},
            "1h": {"trend": "bullish", "signals": ["trend_up"]},
            "15m": {"trend": "bullish", "signals": ["setup_up"]},
            "5m": {"trend": "bullish", "signals": ["entry_up"]},
        },
        "risk_result": {"ok": False, "issues": ["exposure_limit_reached"]},
        "strategy": {"key": "trend_following", "score": 8},
        "strategy_evaluation": {},
        "microstructure_analysis": {"status": "not_available"},
        "news_context": {"items": []},
    })
    assert result["status"] == "completed"
    assert result["consensus"] == "vetoed"
    assert result["vetoed_by"] == ["risk_agent"]
    assert result["action_authority"] == "none"
    assert result["final_decision_owner"] == "TITAN_DecisionEngine"
    assert result["risk_veto_owner"] == "independent_RiskEngine"
    assert len(result["votes"]) == 6


def test_coordinator_fails_closed_for_agent_exceptions():
    coordinator = MultiAgentCoordinator()

    class BrokenAgent:
        name = "broken"
        role = "test"
        def analyze(self, context):
            raise RuntimeError("private detail must not leak")

    coordinator.agents = [BrokenAgent()]
    result = coordinator.deliberate({})
    assert result["status"] == "partial"
    assert result["failed_agents"] == ["broken"]
    assert result["consensus"] == "neutral"
    assert "private detail must not leak" not in str(result)


def test_liquidity_agent_rejects_out_of_range_depth_and_flow_values():
    vote = LiquidityAgent().analyze({
        "microstructure_analysis": {
            "liquidity": {"ok": True, "depth_imbalance": 5.0},
            "order_flow": {"ok": True, "taker_flow_imbalance": -8.0},
        },
    })
    assert vote.bias == "neutral"
    assert not any("imbalance:" in item for item in vote.evidence)
    assert "verified_order_book_metric_invalid" in vote.risk_notes
    assert "verified_order_flow_metric_invalid" in vote.risk_notes


def test_technical_agent_reports_missing_required_timeframe_even_with_extra_frames():
    vote = TechnicalAgent().analyze({
        "timeframe_analyses": {
            "1d": {"trend": "bullish"},
            "4h": {"trend": "bullish"},
            "1h": {"trend": "bullish"},
            "30m": {"trend": "bullish"},
            "2h": {"trend": "bullish"},
        },
    })
    assert "timeframe_evidence_incomplete" in vote.risk_notes
