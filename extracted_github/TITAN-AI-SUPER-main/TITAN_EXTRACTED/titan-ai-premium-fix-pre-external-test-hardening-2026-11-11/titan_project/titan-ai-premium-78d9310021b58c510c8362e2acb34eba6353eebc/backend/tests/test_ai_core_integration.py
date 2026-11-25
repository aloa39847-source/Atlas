from app.integrations import ai_providers
from app.models import db as dbmod
from app.models.entities import DecisionRecord
from app.services.core_service import core_service
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def test_core_analysis_attaches_and_persists_read_only_ai_review(tmp_path, monkeypatch):
    db_path = tmp_path / "ai-core-integration.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, future=True)
    dbmod.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = factory()
    seen = {}

    advisory = {
        "status": "completed",
        "provider": "test-provider",
        "model": "test-model",
        "advisory_text": "Review only; no action change.",
        "action_authority": "none",
        "decision_modified": False,
    }

    def fake_evaluate(session, context):
        seen["session"] = session
        seen["context"] = context
        return advisory

    monkeypatch.setattr(ai_providers, "evaluate_decision_advisory", fake_evaluate)
    try:
        result = core_service.analyze(
            db, symbol="BTC/USDT", market_type="spot", environment_mode="demo",
            data_source="synthetic",
        )
        assert result["ai_evaluation"] == advisory
        assert result["ai_evaluation"]["action_authority"] == "none"
        assert result["ai_evaluation"]["decision_modified"] is False
        assert seen["context"]["decision"]["action"] == result["action"]
        assert seen["context"]["multi_agent_review"] == result["multi_agent_deliberation"]
        assert seen["context"]["multi_agent_review"]["action_authority"] == "none"
        assert seen["context"]["multi_agent_review"]["final_decision_owner"] == "TITAN_DecisionEngine"
        assert seen["context"]["independent_risk_result"] == result.get("risk")
        assert seen["context"]["system_state"]["emergency_stop"] is False
        evidence = seen["context"]["timeframe_evidence"]
        assert len(evidence) == 15
        assert {"1d", "4h", "1h", "15m", "5m"}.issubset(evidence)
        assert all("trend" in item and "signals" in item and "indicators" in item
                   and "structure" in item for item in evidence.values())
        assert seen["context"]["microstructure_evidence"]["status"] == "not_available"

        stored = db.get(DecisionRecord, result["decision_id"])
        assert stored is not None
        assert stored.indicators["ai_evaluation"] == advisory
    finally:
        db.close()
        engine.dispose()
