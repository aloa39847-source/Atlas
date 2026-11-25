from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import db as dbmod
from app.models.entities import LearningRecord
from app.learning.outcomes import build_strategy_memory_context
from app.agents.specialists import StrategyMemoryAgent


def test_strategy_memory_uses_only_valid_confirmed_demo_outcome_records(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'strategy_memory.db'}", future=True)
    dbmod.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = factory()
    try:
        db.add_all([
            LearningRecord(environment_mode="demo", category="trade_outcome", subject="1",
                outcome="win", lesson="closed demo fill",
                evidence={"strategy_key": "ema", "net_pnl": 12.0, "total_fees": 1.0, "source": "binance_demo_confirmed_fills", "research_only": True}),
            LearningRecord(environment_mode="demo", category="trade_outcome", subject="2",
                outcome="loss", lesson="closed demo fill",
                evidence={"strategy_key": "ema", "net_pnl": -4.0, "total_fees": 0.5, "source": "binance_demo_confirmed_fills", "research_only": True}),
            LearningRecord(environment_mode="live", category="trade_outcome", subject="3",
                outcome="win", lesson="not included",
                evidence={"strategy_key": "ema", "net_pnl": 100.0, "total_fees": 0.0, "source": "binance_demo_confirmed_fills", "research_only": True}),
            LearningRecord(environment_mode="demo", category="trade_outcome", subject="4",
                outcome="win", lesson="invalid metric",
                evidence={"strategy_key": "ema", "net_pnl": float("nan"), "total_fees": 0.0, "source": "binance_demo_confirmed_fills", "research_only": True}),
        ])
        db.commit()
        memory = build_strategy_memory_context(db)
        assert set(memory) == {"ema"}
        assert memory["ema"]["sample_count"] == 2
        assert memory["ema"]["wins"] == 1
        assert memory["ema"]["losses"] == 1
        assert memory["ema"]["net_pnl"] == 8.0
        assert memory["ema"]["expectancy_per_trade"] == 4.0
        assert memory["ema"]["research_only"] is True
        assert memory["ema"]["auto_tuning"] is False
    finally:
        db.close()
        engine.dispose()


def test_strategy_memory_agent_reads_realized_metrics_without_directional_authority():
    vote = StrategyMemoryAgent().analyze({
        "strategy": {"key": "ema"},
        "strategy_memory": {
            "ema": {
                "sample_count": 42, "win_rate": 0.55,
                "expectancy_per_trade": 2.5, "research_guidance": "continue_observation",
            },
        },
    })
    assert vote.bias == "neutral"
    assert vote.confidence == 0.0
    assert any("confirmed_demo_sample_count:42" == item for item in vote.evidence)
    assert "no_automatic_parameter_changes" in vote.risk_notes


def test_strategy_memory_agent_does_not_infer_missing_history():
    vote = StrategyMemoryAgent().analyze({
        "strategy": {"key": "new_strategy"}, "strategy_memory": {},
    })
    assert vote.bias == "neutral"
    assert vote.confidence == 0.0
    assert "collect_realized_demo_outcomes_before_adaptive_tuning" in vote.risk_notes
