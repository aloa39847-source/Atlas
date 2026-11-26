from __future__ import annotations

from typing import Any

from app.core.enums import DecisionAction, EngineStatus, EnvironmentMode, MarketType, Timeframe
from .base import Engine
from .risk import RiskEngine
from .strategies import evaluate_strategy_candidates
from .technical import TechnicalAnalysisEngine, TimeframeAnalysis
try:
    from .hybrid.coordinator import HybridIntelligenceCoordinator
except Exception:
    HybridIntelligenceCoordinator = None

try:
    from app.agents.coordinator import MultiAgentCoordinator
except Exception:
    MultiAgentCoordinator = None


CORE_REQUIRED_TIMEFRAMES = ("1d", "4h", "1h", "15m", "5m")


class DecisionEngine(Engine):
    key = "decision_making"
    name = "Decision Making Engine"

    def __init__(self) -> None:
        self.technical = TechnicalAnalysisEngine()
        self.risk = RiskEngine()
        self.hybrid = HybridIntelligenceCoordinator() if HybridIntelligenceCoordinator else None
        self.multi_agent = MultiAgentCoordinator() if MultiAgentCoordinator else None

    @staticmethod
    def _news_confidence_adjustment(news_context: dict[str, Any] | None) -> dict[str, Any]:
        """Bounded 8% confidence modifier from fresh official primary-source news only."""
        if not isinstance(news_context, dict) or not news_context.get("ok"):
            return {"adjustment": 0.0, "weight_cap": 0.08, "used_items": 0,
                    "source_policy": "official_primary_only", "status": "unavailable"}
        weighted_scores = []
        for item in news_context.get("items", []):
            if not isinstance(item, dict) or item.get("source_type") != "official_primary":
                continue
            if item.get("freshness") not in {"fresh", "recent"}:
                continue
            try:
                score = float(item.get("sentiment_score", 0.0))
                confidence = float(item.get("sentiment_confidence", 0.0))
            except (TypeError, ValueError, OverflowError):
                continue
            if not (-1.0 <= score <= 1.0 and 0.0 <= confidence <= 1.0):
                continue
            freshness_weight = 1.0 if item.get("freshness") == "fresh" else 0.5
            weighted_scores.append(score * confidence * freshness_weight)
        if not weighted_scores:
            return {"adjustment": 0.0, "weight_cap": 0.08, "used_items": 0,
                    "source_policy": "official_primary_only", "status": "no_fresh_official_asset_news"}
        raw = (sum(weighted_scores) / len(weighted_scores)) * 0.08
        adjustment = round(max(-0.08, min(0.08, raw)), 4)
        return {"adjustment": adjustment, "weight_cap": 0.08, "used_items": len(weighted_scores),
                "source_policy": "official_primary_only", "status": "applied"}

    def status(self) -> EngineStatus:
        return EngineStatus.IMPLEMENTED_PARTIAL

    def decide(
        self,
        *,
        symbol: str,
        market: MarketType,
        environment: EnvironmentMode,
        timeframe_analyses: dict[str, TimeframeAnalysis],
        balance: float = 1000.0,
        max_leverage: float = 3.0,
        risk_per_trade: float = 0.01,
        batches: dict | None = None,
        microstructure_analysis: dict | None = None,
        open_positions: int = 0,
        current_exposure: float = 0.0,
        max_exposure: float | None = None,
        max_open_positions: int | None = None,
        news_context: dict[str, Any] | None = None,
        strategy_memory: dict[str, Any] | None = None,
        strategy_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Multi-timeframe synthesis (1D trend context -> 4H/1H confirmation -> 15M setup -> 5M timing)
        tf = {k.value if hasattr(k, "value") else str(k): v for k, v in timeframe_analyses.items()}
        # Only the approved 1D/4H/1H/15M/5M stack is mandatory.
        # Other supported intervals enrich context when available.
        supported_tfs = [timeframe.value for timeframe in Timeframe]
        required_tfs = list(CORE_REQUIRED_TIMEFRAMES)
        missing_tfs = [rtf for rtf in required_tfs if tf.get(rtf) is None]
        if missing_tfs:
            return self._wait(
                symbol, market, environment, "required_timeframe_data_missing",
                {"missing_timeframes": missing_tfs}, tf,
            )
        d1 = tf.get("1d"); h4 = tf.get("4h"); h1 = tf.get("1h"); m15 = tf.get("15m"); m5 = tf.get("5m")
        trends = [x.trend for x in [d1, h4, h1] if x]
        bull = sum(1 for t in trends if t == "bullish")
        bear = sum(1 for t in trends if t == "bearish")
        bias = "bullish" if bull > bear else "bearish" if bear > bull else "neutral"
        # Hybrid intelligence layer
        hybrid_analysis = None
        if self.hybrid and batches:
            try:
                hybrid_analysis = self.hybrid.analyze_multi_timeframe(batches, timeframe_analyses, symbol=symbol)
            except Exception as e:
                hybrid_analysis = {"error": str(e), "partial": True}
        
        agent_delib = None

        strategy_evaluation = evaluate_strategy_candidates(
            market, bias, timeframe_analyses=tf,
            microstructure_analysis=microstructure_analysis,
            expected_symbol=symbol, strategy_context=strategy_context,
        )
        selected = strategy_evaluation.get("selected")
        strategy = selected or {
            "key": "none", "name": "No strategy selected",
            "category": "none", "status": strategy_evaluation.get("status"),
        }

        # Optional higher timeframes add context without making all 15 frames mandatory.
        # Two or more opposing higher-timeframe readings veto an otherwise valid setup.
        core_frames = {"1d", "4h", "1h", "15m", "5m"}
        context_frames = tuple(name for name in supported_tfs if name not in core_frames)
        extended_context = {
            name: tf[name].trend for name in context_frames
            if name in tf and getattr(tf[name], "trend", "unknown") in {"bullish", "bearish"}
        }
        higher_context = {"1M", "1w", "3d", "12h", "8h", "6h", "2h"}
        micro_context = {"30m", "3m", "1m"}
        higher_bull = sum(extended_context.get(name) == "bullish" for name in higher_context)
        higher_bear = sum(extended_context.get(name) == "bearish" for name in higher_context)
        micro_bull = sum(extended_context.get(name) == "bullish" for name in micro_context)
        micro_bear = sum(extended_context.get(name) == "bearish" for name in micro_context)
        extended_conflict = (
            (bias == "bullish" and ((higher_bear >= 2 and higher_bear > higher_bull) or micro_bear == 3))
            or (bias == "bearish" and ((higher_bull >= 2 and higher_bull > higher_bear) or micro_bull == 3))
        )
        if extended_conflict:
            return self._wait(
                symbol, market, environment, "extended_timeframe_conflict",
                {"strategy": strategy, "extended_timeframe_context": extended_context,
                 "required_timeframes": required_tfs,
                 "note": "Higher-timeframe conflict or unanimous opposing micro-timeframe confirmation vetoed the setup.",
                 "higher_timeframe_votes": {"bullish": higher_bull, "bearish": higher_bear},
                 "micro_timeframe_votes": {"bullish": micro_bull, "bearish": micro_bear}}, tf,
            )

        if strategy.get("key") == "scalping_m5":
            setup = m5
        elif strategy.get("key") == "swing_h1_h4":
            setup = h1 or h4
        else:
            setup = m15 or m5 or h1
        if not setup:
            return self._wait(symbol, market, environment, "no_timeframe_data", {"strategy": strategy}, timeframe_analyses)
        price = float(setup.indicators.get("close") or 0.0)
        atr = float(setup.indicators.get("atr14") or (price * 0.01) or 1.0)
        if price <= 0:
            return self._wait(symbol, market, environment, "invalid_price", {"strategy": strategy}, timeframe_analyses)

        # Conflict/uncertainty => WAIT (important safety behavior)
        has_conflict = (bull > 0 and bear > 0 and abs(bull - bear) <= 1) or bias == "neutral"
        # Confidence is an evidence score heuristic, not a probability.
        candidate_score = int(strategy.get("score") or 0)
        confidence = 0.35 + min(candidate_score, 8) * 0.035
        if not has_conflict and d1 and h4 and d1.trend == h4.trend:
            confidence += 0.10
        if setup.trend == bias:
            confidence += 0.05
        if strategy.get("key") == "liquidity_orderflow":
            confidence += 0.05
        news_influence = self._news_confidence_adjustment(news_context)
        confidence = round(min(max(confidence + news_influence["adjustment"], 0.0), 0.85), 3)
        # News can modify confidence by at most 8%; it cannot independently
        # create a direction, override a risk veto, or bypass strategy evidence.

        action = DecisionAction.WAIT
        reason = "No clear, risk-acceptable setup after multi-timeframe synthesis."
        if strategy.get("key") == "none":
            reason = "No implemented strategy matched the current evidence; WAIT."
        elif setup.trend != bias:
            reason = "Entry-timeframe trend conflicts with the higher-timeframe bias; WAIT."
        elif not has_conflict and confidence >= 0.55:
            if bias == "bullish":
                action = DecisionAction.BUY if market == MarketType.SPOT else DecisionAction.LONG
                reason = "Higher-timeframe bullish bias with confirming setup; risk checks applied."
            elif bias == "bearish":
                if market == MarketType.SPOT:
                    action = DecisionAction.WAIT
                    reason = "Bearish bias on spot: prefer WAIT/SELL-only-if-position context (no short)."
                else:
                    action = DecisionAction.SHORT
                    reason = "Higher-timeframe bearish bias with confirming setup; futures short considered."

        if market == MarketType.EARN:
            return self._wait(symbol, market, environment, "earn_is_analysis_recommendation_not_trade_execution", {"strategy": strategy, "bias": bias}, timeframe_analyses)

        sl_mult = 1.2
        tp_mult = 2.0
        if action in (DecisionAction.BUY, DecisionAction.LONG):
            stop_loss = price - atr * sl_mult
            take_profit = price + atr * tp_mult
        elif action in (DecisionAction.SELL, DecisionAction.SHORT):
            stop_loss = price + atr * sl_mult
            take_profit = price - atr * tp_mult
        else:
            stop_loss = None
            take_profit = None

        lev = 1.0
        if market == MarketType.FUTURES and action in (DecisionAction.LONG, DecisionAction.SHORT):
            # Independent leverage choice within caps, based on confidence/uncertainty (not fixed 1x).
            lev = 1.0 + min(max((confidence - 0.55) * 10, 0.0), max(0.0, max_leverage - 1.0))
            lev = round(min(lev, max_leverage), 2)
        elif market == MarketType.SPOT:
            lev = 1.0

        risk_result = None
        costs = None
        rr = None
        if action != DecisionAction.WAIT and stop_loss and take_profit:
            risk_result = self.risk.validate_trade(
                environment=environment, market=market, balance=balance, risk_per_trade=risk_per_trade,
                entry=price, stop_loss=stop_loss, leverage=lev,
                position_side="short" if action == DecisionAction.SHORT else "long",
                max_leverage=max_leverage,
                open_positions=open_positions, current_exposure=current_exposure,
                max_exposure=max_exposure, max_open_positions=max_open_positions,
            )
            notional = float(risk_result.get("position_notional_estimate") or 0.0)
            costs = self.risk.estimate_costs(notional, market).to_dict()
            reward = abs(take_profit - price)
            risk_amt = abs(price - stop_loss)
            rr = round(reward / risk_amt, 3) if risk_amt else None
            if not risk_result.get("ok"):
                action = DecisionAction.WAIT
                reason = "Risk engine blocked the setup: " + ",".join(risk_result.get("issues") or [])
            elif rr is not None and rr < 1.2:
                action = DecisionAction.WAIT
                reason = f"Rejected due to weak risk/reward estimate ({rr})."

        # Deliberate only after the deterministic strategy and independent risk
        # result exist, so specialist agents review actual evidence rather than
        # static placeholders. Their consensus is advisory and never changes action.
        if self.multi_agent:
            try:
                agent_delib = self.multi_agent.deliberate({
                    "symbol": symbol,
                    "bias": bias,
                    "timeframe_analyses": {
                        name: {
                            "trend": getattr(analysis, "trend", "unknown"),
                            "signals": list(getattr(analysis, "signals", []) or [])[:12],
                            "indicators": getattr(analysis, "indicators", {}) or {},
                            "structure": getattr(analysis, "structure", {}) or {},
                        }
                        for name, analysis in tf.items()
                    },
                    "microstructure_analysis": microstructure_analysis or {},
                    "risk_result": risk_result,
                    "strategy": strategy,
                    "strategy_evaluation": strategy_evaluation,
                    "news_context": news_context or {},
                    "strategy_memory": strategy_memory or {},
                    "evidence": hybrid_analysis.get("evidence", []) if hybrid_analysis else [],
                })
            except Exception:
                agent_delib = {
                    "status": "unavailable", "consensus": "neutral",
                    "failed_agents": ["coordinator"], "action_authority": "none",
                    "final_decision_owner": "TITAN_DecisionEngine",
                }

        source_map = {k.value if hasattr(k, "value") else str(k): getattr(v.source, "value", str(v.source)) for k, v in (batches or {}).items()}
        batch_markets = {getattr(v, "market_type", "spot") for v in (batches or {}).values()}
        has_synthetic = any(v == "synthetic_labeled" for v in source_map.values())
        has_public = bool(source_map) and all(v == "live_public" for v in source_map.values())
        if has_synthetic:
            source_name = "synthetic_labeled"
            source_warning = "Decision uses LABELED synthetic data; NOT live market data."
        elif has_public and len(batch_markets) == 1 and next(iter(batch_markets)) in {"spot", "futures"}:
            public_market = next(iter(batch_markets))
            source_name = "binance_futures_public" if public_market == "futures" else "binance_public"
            source_warning = (
                "Decision uses Binance USD-M Futures Public read-only candles; no authenticated account data or exchange execution."
                if public_market == "futures"
                else "Decision uses Binance Spot Public read-only candles; no authenticated account data or exchange execution."
            )
        else:
            source_name = "unknown"
            source_warning = "Data-source provenance or market type is missing/mixed; this decision must not be used to authorize an order."
        return {
            "symbol": symbol,
            "environment_mode": environment.value,
            "market_type": market.value,
            "data_source": source_name,
            "action": action.value,
            "strategy": strategy["key"],
            "strategy_name": strategy["name"],
            "strategy_evaluation": strategy_evaluation,
            "confidence": confidence,
            "news_influence": news_influence,
            "confidence_note": "Evidence-score heuristic (0-1), not a calibrated probability, certainty, or profit guarantee.",
            "strategy_evidence_score": candidate_score,
            "extended_timeframe_context": extended_context,
            "optional_timeframes_considered": list(extended_context.keys()),
            "bias": bias,
            "entry_timeframe": setup.timeframe.value,
            "entry_timeframe_aligned": setup.trend == bias,
            "reason": reason,
            "entry_price": price if action != DecisionAction.WAIT else None,
            "stop_loss": round(stop_loss, 8) if stop_loss else None,
            "take_profit": round(take_profit, 8) if take_profit else None,
            "risk_reward": rr,
            "leverage": lev,
            "risk": risk_result,
            "costs": costs,
            "timeframes": {k: {"trend": v.trend, "signals": v.signals[:8], "structure": v.structure}
                           for k, v in tf.items() if v is not None},
            "data_sources": source_map,
            "data_source_warning": source_warning,
            "hybrid_intelligence": hybrid_analysis,
            "multi_agent_deliberation": agent_delib,
            "microstructure_analysis": microstructure_analysis or {"status": "not_available"},
            "timeframes_analyzed_required": required_tfs,
            "timeframe_consensus": {
                "total_frames": len(required_tfs),
                "supported_frames": len(supported_tfs),
                "bullish": sum(1 for name in required_tfs if tf[name].trend == "bullish"),
                "bearish": sum(1 for name in required_tfs if tf[name].trend == "bearish"),
                "sideways_or_unknown": sum(1 for name in required_tfs if tf[name].trend not in {"bullish", "bearish"}),
                "all_required_frames_present": True,
            },
        }

    def _wait(self, symbol, market, environment, reason, extra, tf) -> dict[str, Any]:
        return {
            "symbol": symbol, "environment_mode": environment.value, "market_type": market.value,
            "action": DecisionAction.WAIT.value, "strategy": (extra.get("strategy") or {}).get("key") if isinstance(extra.get("strategy"), dict) else extra.get("strategy"),
            "confidence": 0.0, "reason": reason, "extra": extra,
            "data_source_warning": "WAIT is non-executable; verify source provenance and all five core closed timeframes before any order decision.",
            "timeframes_analyzed_required": list(CORE_REQUIRED_TIMEFRAMES),
            "timeframe_consensus": {
                "supported_frames": len(Timeframe),
                "analyzed_frames": len(tf),
                "bullish": sum(1 for value in tf.values() if value is not None and value.trend == "bullish"),
                "bearish": sum(1 for value in tf.values() if value is not None and value.trend == "bearish"),
                "all_required_frames_present": all(name in tf for name in CORE_REQUIRED_TIMEFRAMES),
            },
            "timeframes": {k: {"trend": v.trend, "signals": v.signals[:8]}
                           for k, v in tf.items() if v is not None},
        }
