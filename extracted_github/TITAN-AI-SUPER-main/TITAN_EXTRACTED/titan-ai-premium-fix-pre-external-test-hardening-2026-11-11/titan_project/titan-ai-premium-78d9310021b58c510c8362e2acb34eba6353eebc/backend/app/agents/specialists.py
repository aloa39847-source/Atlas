"""Evidence-driven specialist agents for TITAN's advisory decision review."""
from __future__ import annotations

import math
from typing import Any

from .base import BaseAgent, AgentVote


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: Any, limit: int = 8) -> list[Any]:
    return value[:limit] if isinstance(value, list) else []


class TechnicalAgent(BaseAgent):
    def __init__(self):
        super().__init__("technical_agent", "Technical Analysis Specialist")

    def analyze(self, context: dict[str, Any]) -> AgentVote:
        frames = _mapping(context.get("timeframe_analyses"))
        required = ("1d", "4h", "1h", "15m", "5m")
        trends = [_mapping(frames.get(key)).get("trend") for key in required]
        bullish = sum(value == "bullish" for value in trends)
        bearish = sum(value == "bearish" for value in trends)
        bias = "bullish" if bullish > bearish else "bearish" if bearish > bullish else "neutral"
        if bias == "neutral":
            bias = context.get("bias", "neutral")
        evidence = []
        for key in required:
            frame = _mapping(frames.get(key))
            trend = frame.get("trend")
            if trend in {"bullish", "bearish"}:
                evidence.append(f"{key}_trend:{trend}")
            for signal in _items(frame.get("signals"), 3):
                if isinstance(signal, (str, int, float)):
                    evidence.append(f"{key}_signal:{str(signal)[:100]}")
        if not evidence:
            evidence = [str(item)[:120] for item in _items(context.get("evidence"), 5)
                        if isinstance(item, (str, int, float))]
        aligned = max(bullish, bearish)
        confidence = min(0.9, 0.25 + aligned * 0.12) if bias != "neutral" else 0.2
        notes = [] if all(key in frames for key in required) else ["timeframe_evidence_incomplete"]
        return AgentVote(self.name, self.role, bias, confidence, evidence[:10],
                         "Derived from supplied closed-timeframe trends and signals; no missing evidence is inferred.",
                         notes)


class LiquidityAgent(BaseAgent):
    def __init__(self):
        super().__init__("liquidity_agent", "Liquidity & Order Flow Specialist")

    def analyze(self, context: dict[str, Any]) -> AgentVote:
        micro = _mapping(context.get("microstructure_analysis"))
        liquidity = _mapping(micro.get("liquidity"))
        flow = _mapping(micro.get("order_flow"))
        evidence = []
        notes = []
        bias = "neutral"
        confidence = 0.15
        if liquidity.get("ok") is True:
            raw = liquidity.get("depth_imbalance")
            valid_depth = (
                isinstance(raw, (int, float)) and not isinstance(raw, bool)
                and math.isfinite(float(raw)) and -1.0 <= float(raw) <= 1.0
            )
            if not valid_depth:
                notes.append("verified_order_book_metric_invalid")
            else:
                evidence.append(f"verified_depth_imbalance:{float(raw):.4f}")
                if raw >= 0.2:
                    bias, confidence = "bullish", min(0.65, 0.35 + float(raw) * 0.3)
                elif raw <= -0.2:
                    bias, confidence = "bearish", min(0.65, 0.35 + abs(float(raw)) * 0.3)
        else:
            notes.append("verified_order_book_unavailable")
        if flow.get("ok") is True:
            raw_flow = flow.get("taker_flow_imbalance")
            valid_flow = (
                isinstance(raw_flow, (int, float)) and not isinstance(raw_flow, bool)
                and math.isfinite(float(raw_flow)) and -1.0 <= float(raw_flow) <= 1.0
            )
            if valid_flow:
                evidence.append(f"recent_taker_flow_imbalance:{float(raw_flow):.4f}")
                if raw_flow >= 0.2:
                    bias = "bullish" if bias != "bearish" else "neutral"
                    confidence = max(confidence, 0.4)
                elif raw_flow <= -0.2:
                    bias = "bearish" if bias != "bullish" else "neutral"
                    confidence = max(confidence, 0.4)
            else:
                notes.append("verified_order_flow_metric_invalid")
        else:
            notes.append("verified_aggregate_trade_flow_unavailable")
        if not evidence:
            evidence.append("no_verified_microstructure_evidence")
        return AgentVote(self.name, self.role, bias, confidence, evidence,
                         "Uses only reconciled public order-book and aggregate-trade metrics; partial snapshots are not treated as a signal.",
                         notes)


class RiskAgent(BaseAgent):
    def __init__(self):
        super().__init__("risk_agent", "Independent Risk Review Specialist")

    def analyze(self, context: dict[str, Any]) -> AgentVote:
        risk = _mapping(context.get("risk_result"))
        if risk.get("ok") is False:
            issues = [str(item)[:100] for item in _items(risk.get("issues"), 6)]
            return AgentVote(self.name, self.role, "neutral", 1.0,
                             ["deterministic_risk_engine_rejected_setup"], 
                             "Reflects the independent risk engine rejection; this agent does not replace that engine.",
                             issues or ["risk_engine_rejected"], veto=True)
        if risk.get("ok") is True:
            return AgentVote(self.name, self.role, "neutral", 0.8,
                             ["deterministic_risk_engine_passed_pretrade_checks"],
                             "Pre-trade risk checks passed; final authorization remains with TITAN risk controls.",
                             ["continue_enforcing_position_sizing", "respect_portfolio_exposure"])
        return AgentVote(self.name, self.role, "neutral", 0.0,
                         ["risk_result_not_available"],
                         "Risk result is unavailable; no approval is inferred.",
                         ["risk_evidence_missing"])


class NewsAgent(BaseAgent):
    def __init__(self):
        super().__init__("news_agent", "News & Fundamental Specialist")

    def analyze(self, context: dict[str, Any]) -> AgentVote:
        news = _mapping(context.get("news_context"))
        items = _items(news.get("items"), 20)
        verified = []
        for item in items:
            row = _mapping(item)
            if row.get("source_type") != "official_primary" or row.get("freshness") not in {"fresh", "recent"}:
                continue
            score = row.get("sentiment_score")
            confidence = row.get("sentiment_confidence")
            if (isinstance(score, bool) or isinstance(confidence, bool)
                    or not isinstance(score, (int, float)) or not isinstance(confidence, (int, float))
                    or not math.isfinite(float(score)) or not math.isfinite(float(confidence))
                    or not -1 <= float(score) <= 1 or not 0 <= float(confidence) <= 1):
                continue
            verified.append((float(score), float(confidence), row.get("freshness")))
        if not verified:
            return AgentVote(self.name, self.role, "neutral", 0.1,
                             ["no_fresh_verified_official_news"],
                             "No fresh verified official primary-source news evidence was available.",
                             ["avoid_trading_on_unverified_news"])
        weighted = [score * confidence * (1.0 if fresh == "fresh" else 0.5)
                    for score, confidence, fresh in verified]
        mean = sum(weighted) / len(weighted)
        bias = "bullish" if mean >= 0.2 else "bearish" if mean <= -0.2 else "neutral"
        return AgentVote(self.name, self.role, bias, min(0.6, abs(mean)),
                         [f"verified_official_news_items:{len(verified)}",
                          f"bounded_weighted_sentiment:{mean:.4f}"],
                         "Summarizes fresh official-source sentiment as contextual evidence only.",
                         ["news_cannot_override_quantitative_decision_or_risk_veto"])


class StrategyAgent(BaseAgent):
    def __init__(self):
        super().__init__("strategy_agent", "Strategy Selection Specialist")

    def analyze(self, context: dict[str, Any]) -> AgentVote:
        strategy = _mapping(context.get("strategy"))
        evaluation = _mapping(context.get("strategy_evaluation"))
        key = strategy.get("key") or "none"
        score = evaluation.get("selected_score", strategy.get("score", 0))
        if isinstance(score, bool):
            score = 0
        try:
            score = float(score)
        except (TypeError, ValueError, OverflowError):
            score = 0.0
        if not math.isfinite(score):
            score = 0.0
        confidence = max(0.0, min(0.75, score / 12.0))
        evidence = [f"selected_strategy:{str(key)[:80]}"]
        if isinstance(strategy.get("category"), str):
            evidence.append(f"strategy_category:{strategy['category'][:80]}")
        if key == "none":
            return AgentVote(self.name, self.role, "neutral", 0.0, evidence,
                             "No strategy candidate was selected from the current evidence.",
                             ["do_not_force_a_strategy"])
        return AgentVote(self.name, self.role, context.get("bias", "neutral"), confidence,
                         evidence, "Evaluates the selected quantitative strategy and its evidence score.",
                         ["strategy_selection_is_advisory_only"])


class StrategyMemoryAgent(BaseAgent):
    def __init__(self):
        super().__init__("strategy_memory_agent", "Historical Trade Memory Specialist")

    def analyze(self, context: dict[str, Any]) -> AgentVote:
        strategy = _mapping(context.get("strategy"))
        key = strategy.get("key")
        memory = _mapping(context.get("strategy_memory"))
        stats = _mapping(memory.get(key)) if isinstance(key, str) else {}
        if not stats:
            return AgentVote(self.name, self.role, "neutral", 0.0,
                             ["no_confirmed_demo_history_for_selected_strategy"],
                             "No confirmed closed Demo outcomes are available for this strategy.",
                             ["collect_realized_demo_outcomes_before_adaptive_tuning"])
        count = stats.get("sample_count", 0)
        expectancy = stats.get("expectancy_per_trade", 0.0)
        win_rate = stats.get("win_rate", 0.0)
        if (isinstance(count, bool) or not isinstance(count, int)
                or isinstance(expectancy, bool) or not isinstance(expectancy, (int, float))
                or not math.isfinite(float(expectancy))
                or isinstance(win_rate, bool) or not isinstance(win_rate, (int, float))
                or not math.isfinite(float(win_rate))):
            return AgentVote(self.name, self.role, "neutral", 0.0,
                             ["historical_trade_memory_invalid"],
                             "Historical outcome metrics failed validation.",
                             ["do_not_use_invalid_learning_metrics"])
        evidence = [
            f"confirmed_demo_sample_count:{count}",
            f"fee_adjusted_expectancy:{float(expectancy):.8f}",
            f"historical_win_rate:{max(0.0, min(1.0, float(win_rate))):.4f}",
        ]
        guidance = stats.get("research_guidance")
        notes = ["historical_memory_is_research_only", "no_automatic_parameter_changes"]
        if count < 30:
            notes.append("insufficient_sample_for_strategy_tuning")
        if float(expectancy) <= 0:
            notes.append("historical_negative_or_zero_expectancy_review")
        if guidance:
            evidence.append(f"research_guidance:{str(guidance)[:80]}")
        return AgentVote(self.name, self.role, "neutral", 0.0, evidence,
                         "Reviews fee-adjusted outcomes from confirmed closed Demo trades; this memory is descriptive, not a trade signal.",
                         notes)
