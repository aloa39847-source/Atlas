"""Multi-agent deliberation with bounded evidence and fail-safe risk veto."""
from __future__ import annotations

import math
from typing import Any

from .specialists import TechnicalAgent, LiquidityAgent, RiskAgent, NewsAgent, StrategyAgent, StrategyMemoryAgent
from .base import AgentVote


class MultiAgentCoordinator:
    def __init__(self):
        self.agents = [
            TechnicalAgent(),
            LiquidityAgent(),
            RiskAgent(),
            NewsAgent(),
            StrategyAgent(),
            StrategyMemoryAgent(),
        ]

    def deliberate(self, context: dict[str, Any]) -> dict[str, Any]:
        votes: list[AgentVote] = []
        failed_agents: list[str] = []
        for agent in self.agents:
            try:
                vote = agent.analyze(context)
                if vote.bias not in {"bullish", "bearish", "neutral"}:
                    raise ValueError("invalid_agent_bias")
                if isinstance(vote.confidence, bool) or not math.isfinite(float(vote.confidence)):
                    raise ValueError("invalid_agent_confidence")
                vote.confidence = max(0.0, min(1.0, float(vote.confidence)))
                vote.evidence = [str(item)[:160] for item in vote.evidence[:10]]
                vote.risk_notes = [str(item)[:160] for item in vote.risk_notes[:10]]
                votes.append(vote)
            except Exception:
                failed_agents.append(agent.name)
                votes.append(AgentVote(
                    agent_name=agent.name, role=agent.role, bias="neutral",
                    confidence=0.0, evidence=["agent_analysis_unavailable"],
                    reasoning="Agent failed safely; no directional evidence was inferred.",
                    risk_notes=["agent_failed"], veto=False,
                ))

        bullish_score = bearish_score = 0.0
        evidence_all: list[str] = []
        vetoes: list[str] = []
        for vote in votes:
            evidence_all.extend(vote.evidence)
            if vote.veto:
                vetoes.append(vote.agent_name)
            evidence_weight = min(1.3, 1.0 + 0.05 * min(len(vote.evidence), 6))
            weighted = vote.confidence * evidence_weight
            if vote.bias == "bullish":
                bullish_score += weighted
            elif vote.bias == "bearish":
                bearish_score += weighted

        if vetoes:
            consensus = "vetoed"
        elif bullish_score > bearish_score * 1.2:
            consensus = "bullish"
        elif bearish_score > bullish_score * 1.2:
            consensus = "bearish"
        else:
            consensus = "neutral"

        return {
            "status": "partial" if failed_agents else "completed",
            "consensus": consensus,
            "vetoed_by": vetoes,
            "failed_agents": failed_agents,
            "bull_score": round(bullish_score, 4),
            "bear_score": round(bearish_score, 4),
            "votes": [{
                "agent": vote.agent_name, "role": vote.role, "bias": vote.bias,
                "confidence": round(vote.confidence, 4),
                "evidence": vote.evidence, "reasoning": vote.reasoning,
                "risk_notes": vote.risk_notes, "veto": vote.veto,
            } for vote in votes],
            "evidence": evidence_all[:40],
            "aggregation_method": "bounded_evidence_weighted_with_independent_risk_reflection",
            "action_authority": "none",
            "final_decision_owner": "TITAN_DecisionEngine",
            "risk_veto_owner": "independent_RiskEngine",
        }
