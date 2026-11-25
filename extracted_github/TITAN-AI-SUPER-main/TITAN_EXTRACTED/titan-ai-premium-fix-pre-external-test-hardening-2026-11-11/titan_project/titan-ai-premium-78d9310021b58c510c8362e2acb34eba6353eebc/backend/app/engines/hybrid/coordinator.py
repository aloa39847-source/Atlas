"""Hybrid Intelligence Coordinator - AI + ML + Statistical + Technical
Combines all components with evidence-based weighting. No random voting.
"""
from __future__ import annotations

from typing import Any

from app.core.enums import EngineStatus, Timeframe
from app.engines.base import Engine
from app.engines.technical import TechnicalAnalysisEngine
from .ml_engine import ml_engine
from .statistical import StatisticalAnalysisEngine


class HybridIntelligenceCoordinator(Engine):
    key = "hybrid_intelligence"
    name = "Hybrid Intelligence Coordinator (AI+ML+Statistical)"

    def __init__(self) -> None:
        self.stat = StatisticalAnalysisEngine()
        self.ml = ml_engine
        self.ta = TechnicalAnalysisEngine()

    def status(self):
        return EngineStatus.IMPLEMENTED_PARTIAL

    def analyze_multi_timeframe(self, batches: dict, analyses: dict, symbol: str | None = None) -> dict[str, Any]:
        """Analyze across all required timeframes with cross-linking."""
        tf_order = [Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5]
        results = {}
        evidence = []
        
        for tf in tf_order:
            if tf in batches:
                batch = batches[tf]
                df = batch.df
                # Get close prices
                try:
                    closes = df["close"] if hasattr(df, "__getitem__") else []
                except Exception:
                    closes = []
                stat_res = self.stat.analyze_series(closes)
                if stat_res.get("ok"):
                    evidence.append(f"{tf.value}_stat: z={stat_res.get('zscore_last')}, vol={stat_res.get('volatility')}")
                # ML patterns and optional trained model prediction
                rows = []
                ml_res = {"patterns": [], "confidence": 0.0, "explainable": True}
                try:
                    rows = df.rows if hasattr(df, "rows") else []
                    ml_res = self.ml.detect_patterns(rows)
                    if ml_res.get("patterns"):
                        evidence.append(f"{tf.value}_ml:{len(ml_res['patterns'])}patterns")
                except Exception:
                    pass
                model_key = f"{symbol}|{tf.value}" if symbol else "default"
                ml_prediction = self.ml.predict(rows, model_key=model_key) if rows else {"ok": False, "error": "no_candles"}
                results[tf.value] = {
                    "statistical": stat_res,
                    "ml_patterns": ml_res,
                    "ml_prediction": ml_prediction,
                    "analysis": analyses.get(tf),
                }
        
        # Cross-timeframe alignment
        trends = []
        for tf in [Timeframe.D1, Timeframe.H4, Timeframe.H1]:
            if tf.value in results and results[tf.value].get("analysis"):
                trends.append(results[tf.value]["analysis"].trend)
        
        bull = sum(1 for t in trends if t == "bullish")
        bear = sum(1 for t in trends if t == "bearish")
        alignment = "aligned_bull" if bull > bear and bull >= 2 else ("aligned_bear" if bear > bull and bear >= 2 else "mixed")
        
        return {
            "timeframes_analyzed": [tf.value for tf in tf_order if tf.value in results],
            "per_timeframe": results,
            "cross_timeframe_alignment": alignment,
            "evidence": evidence,
            "component_contributions": {
                "statistical": "quantitative foundation - deterministic metrics",
                "ml": "explainable pattern recognition",
                "technical": "SMC/candles/indicators structure",
                "ai_llm": "evidence evaluation only (if configured) - never direct execution"
            }
        }
