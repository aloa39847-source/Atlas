from pathlib import Path
import sys

root = Path(sys.argv[1])

service = r'''"""Auditable prediction tracking and performance evaluation.

This service records model decisions and evaluates only explicitly resolved
outcomes. Unverified outcomes never count toward trusted accuracy metrics or
learning-promotion evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import PredictionRecord, PositionRecord


DIRECTION_MAP = {"buy": "bullish", "long": "bullish", "sell": "bearish", "short": "bearish"}


def _valid_price(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if isfinite(price) and price > 0 else None


class PredictionTrackingService:
    """Persist directional forecasts with a reproducible reference price."""

    def record_decision(
        self, db: Session, *, decision_id: int, symbol: str, environment_mode: str,
        market_type: str, action: str, confidence: float | None, reference_price: float,
        horizon: str = "5m", strategy: str | None = None, data_source: str = "unknown",
        stop_loss: float | None = None, take_profit: float | None = None,
    ) -> PredictionRecord | None:
        direction = DIRECTION_MAP.get(str(action or "").lower())
        reference = _valid_price(reference_price)
        if direction is None or reference is None:
            return None
        try:
            conf = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            conf = None
        if conf is not None and (not isfinite(conf) or conf < 0 or conf > 1):
            conf = None
        row = PredictionRecord(
            symbol=symbol.strip().upper(), environment_mode=environment_mode,
            market_type=market_type, horizon=horizon, scenario="base",
            predicted_direction=direction, confidence=conf,
            assumptions={
                "decision_id": int(decision_id), "reference_price": reference,
                "strategy": strategy, "data_source": data_source,
                "stop_loss": _valid_price(stop_loss), "take_profit": _valid_price(take_profit),
                "resolution_policy": "Compare observed price at the declared horizon; no future data used at creation.",
            },
            verified=False,
        )
        db.add(row)
        db.flush()
        return row

    def resolve(self, db: Session, *, prediction_id: int, observed_price: float,
                outcome_source: str) -> dict[str, Any]:
        row = db.get(PredictionRecord, prediction_id)
        if row is None:
            return {"ok": False, "error": "prediction_not_found"}
        if row.resolved_at is not None:
            return {"ok": False, "error": "prediction_already_resolved"}
        observed = _valid_price(observed_price)
        reference = _valid_price((row.assumptions or {}).get("reference_price"))
        if observed is None or reference is None:
            return {"ok": False, "error": "valid_observed_and_reference_prices_required"}
        source = str(outcome_source or "").strip()
        if len(source) < 3:
            return {"ok": False, "error": "outcome_source_required"}
        actual = "bullish" if observed > reference else "bearish" if observed < reference else "neutral"
        correct = actual == row.predicted_direction
        row.outcome = {
            "observed_price": observed, "reference_price": reference,
            "actual_direction": actual, "correct": correct,
            "price_change_pct": round((observed / reference - 1.0) * 100.0, 8),
            "source": source, "verification_status": "pending_admin_review",
        }
        row.resolved_at = datetime.now(timezone.utc)
        row.verified = False
        db.add(row)
        db.flush()
        return {
            "ok": True, "prediction_id": row.id, "correct": correct,
            "actual_direction": actual, "verified": False,
            "note": "Outcome is recorded but excluded from trusted metrics until an administrator verifies its evidence.",
        }

    def verify(self, db: Session, *, prediction_id: int, verification_notes: str) -> dict[str, Any]:
        row = db.get(PredictionRecord, prediction_id)
        if row is None:
            return {"ok": False, "error": "prediction_not_found"}
        if row.resolved_at is None or not isinstance(row.outcome, dict):
            return {"ok": False, "error": "resolved_outcome_required"}
        notes = str(verification_notes or "").strip()
        if len(notes) < 20:
            return {"ok": False, "error": "verification_notes_minimum_20_characters"}
        row.verified = True
        row.outcome = {
            **row.outcome, "verification_status": "manual_admin_review",
            "verification_notes": notes, "verified_at": datetime.now(timezone.utc).isoformat(),
        }
        db.add(row)
        db.flush()
        return {"ok": True, "prediction_id": row.id, "verified": True}


class PerformanceEvaluationService:
    """Compute transparent metrics from verified forecasts and closed shadow positions."""

    def summary(self, db: Session, *, environment_mode: str | None = None,
                market_type: str | None = None, symbol: str | None = None) -> dict[str, Any]:
        prediction_query = select(PredictionRecord)
        position_query = select(PositionRecord).where(
            PositionRecord.status == "closed", PositionRecord.shadow.is_(True)
        )
        if environment_mode:
            prediction_query = prediction_query.where(PredictionRecord.environment_mode == environment_mode)
            position_query = position_query.where(PositionRecord.environment_mode == environment_mode)
        if market_type:
            prediction_query = prediction_query.where(PredictionRecord.market_type == market_type)
            position_query = position_query.where(PositionRecord.market_type == market_type)
        if symbol:
            normalized = symbol.strip().upper()
            prediction_query = prediction_query.where(PredictionRecord.symbol == normalized)
            position_query = position_query.where(PositionRecord.symbol == normalized)
        predictions = db.execute(prediction_query.order_by(PredictionRecord.created_at)).scalars().all()
        resolved = [p for p in predictions if p.resolved_at is not None and isinstance(p.outcome, dict)]
        trusted = [p for p in resolved if p.verified]
        correct = [p for p in trusted if p.outcome.get("correct") is True]
        brier_values = [
            (float(p.confidence) - (1.0 if p.outcome["correct"] else 0.0)) ** 2
            for p in trusted
            if p.confidence is not None and p.outcome.get("correct") in (True, False)
        ]
        positions = db.execute(position_query.order_by(PositionRecord.updated_at)).scalars().all()
        net_pnls = [float(p.realized_pnl or 0.0) - float(p.fees_total or 0.0) for p in positions]
        wins = [pnl for pnl in net_pnls if pnl > 0]
        losses = [pnl for pnl in net_pnls if pnl < 0]
        gross_profit, gross_loss = sum(wins), abs(sum(losses))
        cumulative_pnl = peak_pnl = max_drawdown_quote = 0.0
        for pnl in net_pnls:
            cumulative_pnl += pnl
            peak_pnl = max(peak_pnl, cumulative_pnl)
            max_drawdown_quote = max(max_drawdown_quote, peak_pnl - cumulative_pnl)
        return {
            "filters": {"environment_mode": environment_mode, "market_type": market_type, "symbol": symbol},
            "predictions": {
                "total": len(predictions), "resolved": len(resolved), "verified": len(trusted),
                "unverified_resolved": len(resolved) - len(trusted),
                "directional_accuracy": round(len(correct) / len(trusted), 6) if trusted else None,
                "brier_score": round(sum(brier_values) / len(brier_values), 6) if brier_values else None,
                "note": "Only manually verified outcomes contribute to accuracy and Brier score.",
            },
            "closed_positions": {
                "count": len(positions), "net_pnl_after_fees": round(sum(net_pnls), 8),
                "win_rate": round(len(wins) / len(positions), 6) if positions else None,
                "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss else (None if gross_profit else 0.0),
                "expectancy": round(sum(net_pnls) / len(net_pnls), 8) if net_pnls else None,
                "max_drawdown_quote": round(max_drawdown_quote, 8),
                "scope": "closed_shadow_positions_only",
                "note": "Uses closed shadow PositionRecord values after recorded fees; drawdown is an absolute quote-currency amount, not a percentage or exchange-reconciled account statement.",
            },
        }


prediction_tracking = PredictionTrackingService()
performance_evaluation = PerformanceEvaluationService()
'''

service_path = root / "app/services/performance.py"
service_path.write_text(service)

core_path = root / "app/services/core_service.py"
core = core_path.read_text()
if "from .performance import prediction_tracking" not in core:
    core = core.replace(
        "from .governance import record_config_change\n",
        "from .governance import record_config_change\nfrom .performance import prediction_tracking\n",
        1,
    )
core = core.replace(
    '("performance_evaluation", "Performance Evaluation Engine", "not_implemented"),',
    '("performance_evaluation", "Performance Evaluation Engine", "implemented_partial"),',
)
core = core.replace(
    '("prediction_tracking", "Prediction Tracking Engine", "not_implemented"),',
    '("prediction_tracking", "Prediction Tracking Engine", "implemented_partial"),',
)
if "prediction_tracking.record_decision(" not in core:
    old = """        db.add(rec)
        log_event(db, action="analysis.decision", message=f"Decision {rec.action} for {symbol} ({env.value}/{market.value}).",
                  environment_mode=env.value, market_type=market.value, entity_type="decision", details={"symbol": symbol, "action": rec.action, "confidence": rec.confidence})
        db.commit(); db.refresh(rec)
"""
    new = """        db.add(rec)
        db.flush()
        five_minute = analyses.get(Timeframe.M5.value)
        reference_price = (five_minute.indicators or {}).get("close") if five_minute else None
        if reference_price is not None:
            prediction_tracking.record_decision(
                db, decision_id=rec.id, symbol=symbol, environment_mode=env.value,
                market_type=market.value, action=rec.action, confidence=rec.confidence,
                reference_price=reference_price, horizon=Timeframe.M5.value,
                strategy=rec.strategy, data_source=rec.data_source,
                stop_loss=rec.stop_loss, take_profit=rec.take_profit,
            )
        log_event(db, action="analysis.decision", message=f"Decision {rec.action} for {symbol} ({env.value}/{market.value}).",
                  environment_mode=env.value, market_type=market.value, entity_type="decision", details={"symbol": symbol, "action": rec.action, "confidence": rec.confidence})
        db.commit(); db.refresh(rec)
"""
    if old not in core:
        raise SystemExit("Core analysis persistence marker missing; refusing prediction integration.")
    core = core.replace(old, new, 1)
core_path.write_text(core)

routes_path = root / "app/api/routes.py"
routes = routes_path.read_text()
routes = routes.replace(
    "from app.models.entities import AIProviderConfig, AuditLog, ConfigChange, DecisionRecord, KnowledgeSource, KnowledgeConceptRecord, LearningRecord",
    "from app.models.entities import AIProviderConfig, AuditLog, ConfigChange, DecisionRecord, KnowledgeSource, KnowledgeConceptRecord, LearningRecord, PredictionRecord",
)
if "from app.services.performance import prediction_tracking, performance_evaluation" not in routes:
    routes = routes.replace(
        "from app.services.core_service import core_service\n",
        "from app.services.core_service import core_service\nfrom app.services.performance import prediction_tracking, performance_evaluation\n",
        1,
    )
if "class PredictionResolveRequest(BaseModel):" not in routes:
    marker = """class LearningCreate(BaseModel):
    category: str
    subject: str
    lesson: str = ""
    outcome: str | None = None
    environment_mode: str | None = None
    market_type: str | None = None
    verified: bool = False


"""
    addition = """class PredictionResolveRequest(BaseModel):
    observed_price: float = Field(..., gt=0)
    outcome_source: str = Field(..., min_length=3, max_length=512)


class PredictionVerifyRequest(BaseModel):
    verification_notes: str = Field(..., min_length=20, max_length=2000)


"""
    if marker not in routes:
        raise SystemExit("Prediction request model insertion marker missing.")
    routes = routes.replace(marker, marker + addition, 1)
if '@router.get("/predictions")' not in routes:
    marker = """@router.get("/learning")
def list_learning(db: Session = Depends(get_db)) -> dict[str, Any]:
"""
    additions = """@router.get("/predictions")
def list_predictions(
    limit: int = Query(100, ge=1, le=500),
    environment_mode: str | None = None,
    market_type: str | None = None,
    symbol: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    query = select(PredictionRecord).order_by(desc(PredictionRecord.id))
    if environment_mode:
        query = query.where(PredictionRecord.environment_mode == environment_mode)
    if market_type:
        query = query.where(PredictionRecord.market_type == market_type)
    if symbol:
        query = query.where(PredictionRecord.symbol == symbol.strip().upper())
    rows = db.execute(query.limit(limit)).scalars().all()
    return {"items": [{
        "id": r.id, "created_at": r.created_at.isoformat(),
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        "symbol": r.symbol, "environment_mode": r.environment_mode,
        "market_type": r.market_type, "horizon": r.horizon,
        "predicted_direction": r.predicted_direction, "confidence": r.confidence,
        "assumptions": r.assumptions, "outcome": r.outcome, "verified": r.verified,
    } for r in rows]}


@router.post("/predictions/{prediction_id}/resolve")
def resolve_prediction(
    prediction_id: int, payload: PredictionResolveRequest,
    db: Session = Depends(get_db), _user=Depends(get_current_user),
) -> dict[str, Any]:
    result = prediction_tracking.resolve(
        db, prediction_id=prediction_id, observed_price=payload.observed_price,
        outcome_source=payload.outcome_source,
    )
    if not result.get("ok"):
        status = 404 if result.get("error") == "prediction_not_found" else 400
        raise HTTPException(status_code=status, detail=result.get("error"))
    db.commit()
    return result


@router.post("/predictions/{prediction_id}/verify")
def verify_prediction(
    prediction_id: int, payload: PredictionVerifyRequest,
    db: Session = Depends(get_db), user=Depends(get_current_user),
) -> dict[str, Any]:
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="admin_verification_required")
    result = prediction_tracking.verify(
        db, prediction_id=prediction_id, verification_notes=payload.verification_notes,
    )
    if not result.get("ok"):
        status = 404 if result.get("error") == "prediction_not_found" else 400
        raise HTTPException(status_code=status, detail=result.get("error"))
    db.commit()
    return result


@router.get("/performance/summary")
def performance_summary(
    environment_mode: str | None = None,
    market_type: str | None = None,
    symbol: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return performance_evaluation.summary(
        db, environment_mode=environment_mode, market_type=market_type, symbol=symbol,
    )


"""
    if marker not in routes:
        raise SystemExit("Prediction API route insertion marker missing.")
    routes = routes.replace(marker, additions + marker, 1)
routes_path.write_text(routes)

tests = r'''from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.db import Base
from app.models.entities import PredictionRecord, PositionRecord
from app.services.performance import PredictionTrackingService, PerformanceEvaluationService


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'prediction-performance.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_prediction_record_resolve_and_manual_verification(tmp_path):
    db = _session(tmp_path)
    try:
        service = PredictionTrackingService()
        row = service.record_decision(
            db, decision_id=7, symbol="BTC/USDT", environment_mode="demo", market_type="spot",
            action="buy", confidence=0.82, reference_price=50000, horizon="5m",
            strategy="trend_following", data_source="synthetic_labeled",
        )
        db.commit()
        assert row is not None and row.predicted_direction == "bullish" and row.verified is False
        result = service.resolve(db, prediction_id=row.id, observed_price=50100, outcome_source="paper candle close")
        db.commit()
        assert result["ok"] is True and result["correct"] is True
        assert row.outcome["price_change_pct"] > 0 and row.verified is False
        assert service.resolve(db, prediction_id=row.id, observed_price=50200, outcome_source="duplicate")["error"] == "prediction_already_resolved"
        verified = service.verify(db, prediction_id=row.id, verification_notes="Reviewed against an independent archived candle source.")
        db.commit()
        assert verified["ok"] is True and row.verified is True
    finally:
        db.close()


def test_prediction_rejects_invalid_reference_and_observed_prices(tmp_path):
    db = _session(tmp_path)
    try:
        service = PredictionTrackingService()
        assert service.record_decision(
            db, decision_id=1, symbol="BTC/USDT", environment_mode="demo", market_type="spot",
            action="buy", confidence=0.7, reference_price=0,
        ) is None
        row = service.record_decision(
            db, decision_id=2, symbol="BTC/USDT", environment_mode="demo", market_type="spot",
            action="buy", confidence=0.7, reference_price=100,
        )
        db.commit()
        result = service.resolve(db, prediction_id=row.id, observed_price=float("nan"), outcome_source="source")
        assert result["ok"] is False and result["error"] == "valid_observed_and_reference_prices_required"
        assert row.resolved_at is None
    finally:
        db.close()


def test_performance_summary_uses_only_verified_predictions_and_shadow_positions(tmp_path):
    db = _session(tmp_path)
    try:
        service = PredictionTrackingService()
        verified = service.record_decision(
            db, decision_id=1, symbol="BTC/USDT", environment_mode="demo", market_type="spot",
            action="buy", confidence=0.8, reference_price=100,
        )
        unverified = service.record_decision(
            db, decision_id=2, symbol="BTC/USDT", environment_mode="demo", market_type="spot",
            action="sell", confidence=0.7, reference_price=100,
        )
        db.flush()
        service.resolve(db, prediction_id=verified.id, observed_price=105, outcome_source="archived candle")
        service.verify(db, prediction_id=verified.id, verification_notes="Cross-checked with two independent candle records.")
        service.resolve(db, prediction_id=unverified.id, observed_price=95, outcome_source="unreviewed source")
        db.add(PositionRecord(environment_mode="demo", market_type="spot", symbol="BTC/USDT", side="long_spot",
                              quantity=0, entry_avg_price=100, mark_price=105, realized_pnl=10,
                              fees_total=1, status="closed", shadow=True))
        db.add(PositionRecord(environment_mode="demo", market_type="spot", symbol="BTC/USDT", side="long_spot",
                              quantity=0, entry_avg_price=100, mark_price=105, realized_pnl=999,
                              fees_total=0, status="closed", shadow=False))
        db.commit()
        result = PerformanceEvaluationService().summary(db, environment_mode="demo", market_type="spot")
        assert result["predictions"]["total"] == 2
        assert result["predictions"]["resolved"] == 2
        assert result["predictions"]["verified"] == 1
        assert result["predictions"]["directional_accuracy"] == 1.0
        assert result["closed_positions"]["count"] == 1
        assert result["closed_positions"]["net_pnl_after_fees"] == 9.0
        assert result["closed_positions"]["scope"] == "closed_shadow_positions_only"
    finally:
        db.close()


def test_non_directional_wait_decisions_do_not_create_predictions(tmp_path):
    db = _session(tmp_path)
    try:
        service = PredictionTrackingService()
        result = service.record_decision(
            db, decision_id=1, symbol="BTC/USDT", environment_mode="demo", market_type="spot",
            action="wait", confidence=0.9, reference_price=100,
        )
        assert result is None
        assert db.execute(select(PredictionRecord)).scalars().all() == []
    finally:
        db.close()
'''
(root / "tests/test_prediction_performance.py").write_text(tests)
