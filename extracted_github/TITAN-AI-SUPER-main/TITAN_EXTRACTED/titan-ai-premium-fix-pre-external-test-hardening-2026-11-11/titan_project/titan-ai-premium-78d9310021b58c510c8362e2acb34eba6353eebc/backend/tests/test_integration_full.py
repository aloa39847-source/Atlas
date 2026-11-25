"""Full integration test - data/engines/decision/db/dashboard flow"""
from __future__ import annotations

from app.core.enums import DataSourceKind, DecisionAction, EnvironmentMode, MarketType, Timeframe
from app.engines.decision import DecisionEngine
from app.engines.market_data import MarketDataEngine
from app.engines.risk import RiskEngine
from app.engines.technical import TechnicalAnalysisEngine


def test_full_integration_flow():
    md = MarketDataEngine()
    ta = TechnicalAnalysisEngine()
    de = DecisionEngine()
    risk = RiskEngine()
    
    # All required timeframes
    batches = md.get_multi_timeframe("BTC/USDT", seed=123)
    assert set(b.source for b in batches.values()) == {DataSourceKind.SYNTHETIC_LABELED}
    for tf in [Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5]:
        assert tf in batches
    
    # Analysis for all timeframes
    analyses = {tf: ta.analyze(batches[tf].df, tf) for tf in batches}
    for tf, a in analyses.items():
        assert a.timeframe == tf
        assert "bos_choch" in a.structure or "swing_tags" in a.structure
    
    # Decision with hybrid
    result = de.decide(
        symbol="BTC/USDT",
        market=MarketType.SPOT,
        environment=EnvironmentMode.DEMO,
        timeframe_analyses=analyses,
        batches=batches,
    )
    assert result["action"] in {a.value for a in DecisionAction}
    assert result["data_source_warning"]
    assert result["action"] == "wait" or result.get("hybrid_intelligence") is not None or result.get("multi_agent_deliberation") is not None
    assert len(result.get("timeframes_analyzed_required", [])) == 5
    assert result["timeframe_consensus"]["supported_frames"] == 15
    
    # Risk checks
    spot_check = risk.validate_trade(environment=EnvironmentMode.DEMO, market=MarketType.SPOT, balance=1000, risk_per_trade=0.01, entry=100, stop_loss=99, leverage=5)
    assert spot_check["leverage_used"] == 1.0
    earn_check = risk.validate_trade(environment=EnvironmentMode.DEMO, market=MarketType.EARN, balance=1000, risk_per_trade=0.01, entry=100, stop_loss=99, leverage=1)
    assert earn_check["ok"] is False
    
    print("FULL_INTEGRATION_TEST_PASSED")

