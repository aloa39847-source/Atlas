from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import EnvironmentMode, MarketType
from app.models.db import get_db
from app.models.entities import AIProviderConfig, AuditLog, ConfigChange, DecisionRecord, KnowledgeSource, LearningRecord, TradeRecord, PositionRecord, PortfolioSnapshot
from app.services import auth_service
from app.integrations.ai_providers import SUPPORTED_PROVIDERS, invoke_provider, list_configs, record_usage
from app.services.core_service import core_service
from app.engines.arbitrage import ArbitrageAnalysisEngine

router = APIRouter()


# --- Auth (private access) ---

class LoginRequest(BaseModel):
    email: str
    password: str
    totp_code: str | None = None


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return authorization.strip()


def get_current_user(request: Request, db: Session = Depends(get_db), authorization: str | None = Header(default=None)):
    token = _bearer(authorization) or request.cookies.get("titan_session")
    user = auth_service.get_session_user(db, token)
    if auth_service.auth_required() and not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user




class StateUpdate(BaseModel):
    environment_mode: str | None = Field(None, description="demo|live; Live account monitoring is read-only and Live order execution is hard-disabled in this release. Binance Testnet is selected separately via account_target.")
    market_type: str | None = Field(None, description="spot|futures|earn")
    autonomous_enabled: bool | None = None
    emergency_stop: bool | None = None


class AnalyzeRequest(BaseModel):
    symbol: str = Field(..., json_schema_extra={"example": "BTC/USDT"})
    market_type: str = Field("spot")
    environment_mode: str = Field("demo")
    balance: float = Field(1000.0, gt=0)
    max_leverage: float = Field(3.0, ge=1)
    risk_per_trade: float = Field(0.01, gt=0, le=0.1)
    data_source: str = Field("synthetic", description="synthetic or binance_public (read-only)")


class ArbitrageRequest(BaseModel):
    route: list[str] = Field(..., min_length=4, max_length=4, json_schema_extra={"example": ["USDT", "BTC", "ETH", "USDT"]})
    starting_amount: float = Field(1000.0, gt=0)
    fee_rate: float = Field(0.001, ge=0, lt=1)
    slippage_rate: float = Field(0.0005, ge=0, lt=1)
    min_profit_pct: float = Field(0.05, ge=0)


class KnowledgeCreate(BaseModel):
    title: str
    source_type: str = "manual"
    url: str | None = None
    reliability_score: float | None = Field(None, ge=0, le=1)
    verified: bool = False
    notes: str = ""


class LearningCreate(BaseModel):
    category: str
    subject: str
    lesson: str = ""
    outcome: str | None = None
    environment_mode: str | None = None
    market_type: str | None = None
    verified: bool = False


class StrategyVersionCreate(BaseModel):
    params: dict[str, Any]
    metrics: dict[str, Any]
    make_active: bool = False
    explicit_approval: bool = False


class StrategyProposalCreate(BaseModel):
    current_params: dict[str, Any]
    proposed_params: dict[str, Any]
    rationale: str = Field(..., min_length=1, max_length=2000)
    metrics_before: dict[str, Any]


class StrategyProposalTest(BaseModel):
    metrics_after_paper: dict[str, Any]


class StrategyProposalWalkForward(BaseModel):
    baseline_folds: list[dict[str, Any]]
    candidate_folds: list[dict[str, Any]]
    min_folds: int = 3
    min_samples_per_fold: int = 30


class StrategyApproval(BaseModel):
    explicit_approval: bool = False


@router.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "titan-ai-premium", "version": "0.1.0-foundation"}


@router.get("/system/status")
def system_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return sanitized, observed configuration without sending exchange orders."""
    from app.integrations.ai_providers import provider_status
    from app.integrations.local_ai import local_model_status
    from app.services.credentials import load_binance_credentials

    state = core_service.get_state(db)
    target = str(state.get("account_target") or "demo").lower()
    market = str(state.get("market_type") or "spot").lower()

    # Credential presence is not a connection test. Never expose decrypted keys.
    if target in {"testnet", "live"}:
        try:
            credentials = load_binance_credentials(environment=target, market=market)
            configured = bool(credentials.get("configured"))
        except Exception:
            configured = False
        live_server_enabled = False
        live_user_confirmed = bool(state.get("live_confirmed_by_user")) if target == "live" else False
        binance = {
            "status": "credentials_configured_not_connected" if configured else "needs_credentials",
            "configured": configured,
            "account_target": target,
            "market": market,
            "connection_checked": False,
            "orders_submitted": False,
            "server_live_enabled": live_server_enabled,
            "user_live_confirmed": live_user_confirmed,
            "live_start_required": False,
            "live_execution_hard_disabled": True,
            "execution_ready": False,
            "note": (
                "Live credentials are for read-only monitoring only; Live order execution is hard-disabled in this release."
                if target == "live" else
                "Testnet credentials are stored separately; this status request does not submit orders."
            ),
        }
    else:
        binance = {
            "status": "demo_shadow",
            "configured": False,
            "account_target": "demo",
            "market": market,
            "connection_checked": False,
            "orders_submitted": False,
            "note": "Demo/Shadow mode does not require private Binance credentials.",
        }

    # All enabled analysis advisors are listed; readiness and successful
    # connection tests are reported separately.
    try:
        ai_snapshot = provider_status(db)
        provider_rows = ai_snapshot.get("providers") or []
    except Exception:
        provider_rows = []
    analysis_rows = []
    for item in provider_rows:
        if not item.get("enabled"):
            continue
        tasks = item.get("tasks") or []
        if tasks and not {str(task).strip().lower() for task in tasks}.intersection(
            {"analysis", "decision", "trading_analysis"}
        ):
            continue
        analysis_rows.append(item)
    try:
        local = local_model_status()
    except Exception:
        local = {"enabled": False, "connected": False, "model_available": False, "status": "unavailable"}
    local_ready = bool(
        local.get("enabled") is True
        and local.get("connected") is True
        and local.get("model_available") is True
    )
    tested_count = sum(
        1 for item in analysis_rows
        if item.get("ready") and item.get("last_test_success") is True
    )
    if analysis_rows and tested_count == len(analysis_rows):
        ai_status = "connection_tested"
    elif analysis_rows:
        ai_status = "configured_not_fully_tested"
    elif local_ready:
        ai_status = "local_only_fallback"
    else:
        ai_status = "not_configured"
    ai = {
        "status": ai_status,
        "configured": bool(analysis_rows),
        "enabled_analysis_advisors": len(analysis_rows),
        "connection_tested_count": tested_count,
        "providers": [
            {
                "provider": item.get("provider"),
                "model": item.get("model"),
                "enabled": bool(item.get("enabled")),
                "tasks": item.get("tasks") or [],
                "key_configured": bool(item.get("key_configured")),
                "connection_tested": item.get("last_test_success") is True,
            }
            for item in provider_rows
        ],
        "local_model": {
            "enabled": bool(local.get("enabled")),
            "connected": bool(local.get("connected")),
            "model_available": bool(local.get("model_available")),
            "model": local.get("model"),
            "status": local.get("status"),
            "api_key_required": False,
        },
        "action_authority": "none",
        "note": "AI providers are read-only advisors; deterministic TITAN decision and risk engines retain authority.",
    }

    telegram_configured = bool(
        str(settings.telegram_bot_token or "").strip()
        and str(settings.telegram_allowed_chat_ids or "").strip()
    )
    integrations = {
        "binance": binance,
        "telegram": {
            "status": "configured_not_connection_tested" if telegram_configured else "needs_external_service",
            "configured": telegram_configured,
            "connection_checked": False,
            "note": "Bot token and allowed chat IDs are required; secrets are never returned.",
        },
        "ai_providers": ai,
    }
    return {
        "state": state,
        "engines": core_service.engines(db),
        "integrations": integrations,
        "data_policy": {
            "live_market_data": False,
            "public_market_data_supported": True,
            "public_market_data_provider": "Binance public Spot and USD-M Futures endpoints",
            "connection_checked": False,
            "synthetic_labeled_available": True,
            "note": (
                "This status request does not probe market-feed connectivity. Public read-only Binance "
                "data is supported; analysis validates source provenance, market type, timeframe and freshness. "
                "Synthetic data remains explicitly labeled and cannot authorize exchange orders."
            ),
        },
    }


@router.post("/system/state")
def update_state(payload: StateUpdate, db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    try:
        return core_service.set_state(
            db,
            environment_mode=payload.environment_mode,
            market_type=payload.market_type,
            autonomous_enabled=payload.autonomous_enabled,
            emergency_stop=payload.emergency_stop,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/strategies")
def strategies() -> dict[str, Any]:
    return {"strategies": core_service.strategies(), "note": "Registry is extensible; statuses reflect current implementation/integration state."}


@router.post("/analysis/arbitrage")
def analyze_arbitrage(payload: ArbitrageRequest, _user=Depends(get_current_user)) -> dict[str, Any]:
    return ArbitrageAnalysisEngine().fetch_and_analyze(
        payload.route,
        starting_amount=payload.starting_amount,
        fee_rate=payload.fee_rate,
        slippage_rate=payload.slippage_rate,
        min_profit_pct=payload.min_profit_pct,
    )


@router.post("/analysis/decision")
def analyze(payload: AnalyzeRequest, db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    try:
        return core_service.analyze(
            db,
            symbol=payload.symbol,
            market_type=payload.market_type,
            environment_mode=payload.environment_mode,
            balance=payload.balance,
            max_leverage=payload.max_leverage,
            risk_per_trade=payload.risk_per_trade,
            data_source=payload.data_source,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/decisions")
def list_decisions(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    rows = db.execute(select(DecisionRecord).order_by(desc(DecisionRecord.id)).limit(limit)).scalars().all()
    return {"items": [
        {"id": r.id, "created_at": r.created_at.isoformat(), "symbol": r.symbol, "action": r.action, "strategy": r.strategy,
         "confidence": r.confidence, "environment_mode": r.environment_mode, "market_type": r.market_type,
         "data_source": r.data_source, "reason": r.rationale}
        for r in rows
    ]}


@router.get("/audit")
def list_audit(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    rows = db.execute(select(AuditLog).order_by(desc(AuditLog.id)).limit(limit)).scalars().all()
    return {"items": [
        {"id": r.id, "created_at": r.created_at.isoformat(), "action": r.action, "severity": r.severity,
         "message": r.message, "environment_mode": r.environment_mode, "market_type": r.market_type, "details": r.details}
        for r in rows
    ]}


@router.get("/knowledge")
def list_knowledge(db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    rows = db.execute(select(KnowledgeSource).order_by(desc(KnowledgeSource.id)).limit(200)).scalars().all()
    return {"items": [{"id": r.id, "title": r.title, "source_type": r.source_type, "url": r.url, "reliability_score": r.reliability_score, "verified": r.verified, "notes": r.notes} for r in rows]}


@router.post("/knowledge")
def add_knowledge(payload: KnowledgeCreate, db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    r = KnowledgeSource(title=payload.title, source_type=payload.source_type, url=payload.url, reliability_score=payload.reliability_score, verified=payload.verified, notes=payload.notes)
    db.add(r); db.commit(); db.refresh(r)
    return {"id": r.id, "title": r.title}


@router.get("/learning")
def list_learning(db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    rows = db.execute(select(LearningRecord).order_by(desc(LearningRecord.id)).limit(200)).scalars().all()
    return {"items": [{"id": r.id, "category": r.category, "subject": r.subject, "outcome": r.outcome, "lesson": r.lesson, "verified": r.verified, "environment_mode": r.environment_mode, "market_type": r.market_type} for r in rows]}


@router.post("/learning")
def add_learning(payload: LearningCreate, db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    r = LearningRecord(category=payload.category, subject=payload.subject, lesson=payload.lesson, outcome=payload.outcome, environment_mode=payload.environment_mode, market_type=payload.market_type, verified=payload.verified)
    db.add(r); db.commit(); db.refresh(r)
    return {"id": r.id, "subject": r.subject}


@router.get("/learning/strategies/{strategy_key}/versions")
def list_strategy_versions(strategy_key: str, db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    from app.learning.persistent import PersistentStrategyLearning
    return {"items": PersistentStrategyLearning().list_versions(db, strategy_key)}


@router.get("/learning/strategies/{strategy_key}/active")
def get_active_strategy_version(strategy_key: str, db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    from app.learning.persistent import PersistentStrategyLearning
    result = PersistentStrategyLearning().get_active_version(db, strategy_key)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "active_version_not_found"))
    return result


@router.post("/learning/strategies/{strategy_key}/versions")
def create_strategy_version(strategy_key: str, payload: StrategyVersionCreate, db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    from app.learning.persistent import PersistentStrategyLearning
    result = PersistentStrategyLearning().create_version(db, strategy_key, payload.params, payload.metrics,
        make_active=payload.make_active, explicit_approval=payload.explicit_approval)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "strategy_version_create_failed"))
    return result


@router.post("/learning/strategies/{strategy_key}/rollback")
def rollback_strategy_version(strategy_key: str, payload: StrategyApproval, version: int = Query(..., ge=1),
                              db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    from app.learning.persistent import PersistentStrategyLearning
    result = PersistentStrategyLearning().rollback(db, strategy_key, version, explicit_approval=payload.explicit_approval)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "strategy_rollback_failed"))
    return result


@router.get("/learning/strategies/{strategy_key}/proposals")
def list_strategy_proposals(strategy_key: str, db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    rows = db.execute(select(LearningRecord).where(LearningRecord.category == "strategy_proposal",
        LearningRecord.subject == strategy_key).order_by(desc(LearningRecord.id)).limit(100)).scalars().all()
    return {"items": [{"id": row.id, "strategy_key": row.subject, "status": row.outcome,
        "rationale": row.lesson, "evidence": row.evidence,
        "created_at": row.created_at.isoformat() if row.created_at else None} for row in rows]}


@router.post("/learning/strategies/{strategy_key}/proposals")
def create_strategy_proposal(strategy_key: str, payload: StrategyProposalCreate,
                             db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    from app.learning.persistent import PersistentStrategyLearning
    result = PersistentStrategyLearning().create_proposal(db, strategy_key, payload.current_params,
        payload.proposed_params, payload.rationale, payload.metrics_before)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "strategy_proposal_failed"))
    return result


@router.post("/learning/proposals/{proposal_id}/test")
def test_strategy_proposal(proposal_id: int, payload: StrategyProposalTest,
                           db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    from app.learning.persistent import PersistentStrategyLearning
    result = PersistentStrategyLearning().test_proposal(db, proposal_id, payload.metrics_after_paper)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "strategy_proposal_test_failed"))
    return result


@router.post("/learning/proposals/{proposal_id}/walk-forward")
def validate_strategy_proposal_walk_forward(proposal_id: int, payload: StrategyProposalWalkForward,
                                             db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    from app.learning.persistent import PersistentStrategyLearning
    result = PersistentStrategyLearning().validate_walk_forward(db, proposal_id, payload.baseline_folds,
        payload.candidate_folds, min_folds=payload.min_folds, min_samples_per_fold=payload.min_samples_per_fold)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "strategy_walk_forward_failed"))
    return result


@router.post("/learning/proposals/{proposal_id}/apply-to-paper")
def apply_strategy_proposal_to_paper(proposal_id: int, payload: StrategyApproval,
                                     db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    from app.learning.persistent import PersistentStrategyLearning
    result = PersistentStrategyLearning().apply_proposal_to_paper(db, proposal_id,
        explicit_approval=payload.explicit_approval)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "strategy_proposal_apply_failed"))
    return result


@router.get("/learning/strategies/{strategy_key}/outcomes")
def strategy_trade_outcomes(
    strategy_key: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
) -> dict[str, Any]:
    from app.learning.outcomes import summarize_strategy_outcomes
    result = summarize_strategy_outcomes(db, strategy_key)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "invalid_strategy_key"))
    return result


# --- Auth endpoints ---

@router.get("/auth/status")
def auth_status(db: Session = Depends(get_db)):
    from app.models.entities import AppUser
    has_user = db.execute(select(AppUser.id).limit(1)).first() is not None
    return {
        "auth_required": auth_service.auth_required(),
        "allowlist_configured": bool(auth_service.get_allowed_emails()) or bool(settings.allowed_emails),
        "has_user": has_user,
        "note": "Set initial password locally with backend script. Never send passwords in chat.",
    }


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else None
    retry_after = auth_service.login_rate_limit_remaining(client_ip, payload.email)
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    user = auth_service.authenticate(db, payload.email, payload.password)
    if not user:
        auth_service.record_login_attempt(client_ip, payload.email, success=False)
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials, email not allowed, or account locked")
    if not auth_service.verify_login_second_factor(user, payload.totp_code):
        auth_service.log_event(
            db, action="auth.2fa_failed", actor=auth_service.normalize_email(user.email),
            severity="warning", message="Login denied: second-factor verification failed.",
        )
        auth_service.record_login_attempt(client_ip, payload.email, success=False)
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials, email not allowed, or account locked")
    auth_service.record_login_attempt(client_ip, payload.email, success=True)
    token = auth_service.create_session(db, user, ttl_hours=settings.session_ttl_hours, ip_address=client_ip, user_agent=request.headers.get("user-agent"))
    db.commit()
    return {"ok": True, "token": token, "email": user.email, "token_type": "bearer"}


@router.post("/auth/logout")
def logout(db: Session = Depends(get_db), authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    ok = auth_service.revoke_session(db, token) if token else False
    db.commit()
    return {"ok": ok}


@router.get("/auth/me")
def me(user=Depends(get_current_user)):
    if not user:
        return {"authenticated": False, "auth_required": auth_service.auth_required()}
    return {"authenticated": True, "email": user.email, "is_admin": user.is_admin, "two_factor_enabled": user.two_factor_enabled}


@router.get("/config/changes")
def config_changes(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db), _user=Depends(get_current_user)):
    rows = db.execute(select(ConfigChange).order_by(desc(ConfigChange.id)).limit(limit)).scalars().all()
    return {"items": [{"id": r.id, "created_at": r.created_at.isoformat(), "actor": r.actor, "scope": r.scope, "key": r.key, "approved": r.approved, "reason": r.reason, "old_value": r.old_value, "new_value": r.new_value} for r in rows]}


@router.get("/knowledge/concepts")
def knowledge_concepts(category: str | None = Query(None), q: str | None = Query(None)):
    from app.knowledge.base import categories, list_concepts
    return {"categories": categories(), "items": list_concepts(category=category, query=q)}


@router.get("/chat/context")
def chat_context(db: Session = Depends(get_db), _user=Depends(get_current_user)):
    # Grounded context for a system-aware assistant (no external LLM required).
    state = core_service.get_state(db)
    decisions = db.execute(select(DecisionRecord).order_by(desc(DecisionRecord.id)).limit(10)).scalars().all()
    audits = db.execute(select(AuditLog).order_by(desc(AuditLog.id)).limit(15)).scalars().all()
    return {
        "state": state,
        "recent_decisions": [{"id": d.id, "symbol": d.symbol, "action": d.action, "strategy": d.strategy, "confidence": d.confidence, "reason": d.rationale, "data_source": d.data_source, "created_at": d.created_at.isoformat()} for d in decisions],
        "recent_audit": [{"action": a.action, "severity": a.severity, "message": a.message, "created_at": a.created_at.isoformat()} for a in audits],
        "note": "This context is derived from real system records in this environment. No external LLM response is fabricated here.",
    }


class ChatRequest(BaseModel):
    message: str
    language: str | None = None


@router.get("/chat/reports")
def chat_reports(limit: int = Query(25, ge=1, le=100), db: Session = Depends(get_db), _user=Depends(get_current_user)) -> dict[str, Any]:
    decisions = db.execute(select(DecisionRecord).order_by(desc(DecisionRecord.id)).limit(limit)).scalars().all()
    audits = db.execute(select(AuditLog).order_by(desc(AuditLog.id)).limit(limit)).scalars().all()
    trades = db.execute(select(TradeRecord).order_by(desc(TradeRecord.id)).limit(limit)).scalars().all()
    positions = db.execute(select(PositionRecord).order_by(desc(PositionRecord.id)).limit(limit)).scalars().all()
    snapshots = db.execute(select(PortfolioSnapshot).order_by(desc(PortfolioSnapshot.id)).limit(5)).scalars().all()
    from datetime import datetime, timezone
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decisions": [{"id": r.id, "created_at": r.created_at.isoformat() if r.created_at else None,
            "symbol": r.symbol, "action": r.action, "strategy": r.strategy, "confidence": r.confidence,
            "entry_price": r.entry_price, "stop_loss": r.stop_loss, "take_profit": r.take_profit,
            "leverage": r.leverage, "rationale": r.rationale, "data_source": r.data_source,
            "environment_mode": r.environment_mode, "market_type": r.market_type} for r in decisions],
        "audit": [{"id": r.id, "created_at": r.created_at.isoformat() if r.created_at else None,
            "action": r.action, "severity": r.severity, "message": r.message,
            "environment_mode": r.environment_mode, "market_type": r.market_type, "details": r.details} for r in audits],
        "trades": [{"id": r.id, "created_at": r.created_at.isoformat() if r.created_at else None,
            "closed_at": r.closed_at.isoformat() if r.closed_at else None, "symbol": r.symbol,
            "side": r.side, "status": r.status, "strategy": r.strategy, "quantity": r.quantity,
            "entry_price": r.entry_price, "exit_price": r.exit_price, "leverage": r.leverage,
            "fees_total": r.fees_total, "pnl_gross": r.pnl_gross, "pnl_net": r.pnl_net, "reason": r.reason,
            "metadata": r.metadata_json} for r in trades],
        "positions": [{"id": r.id, "symbol": r.symbol, "side": r.side, "status": r.status,
            "quantity": r.quantity, "entry_avg_price": r.entry_avg_price, "mark_price": r.mark_price,
            "leverage": r.leverage, "unrealized_pnl": r.unrealized_pnl, "realized_pnl": r.realized_pnl,
            "environment_mode": r.environment_mode, "market_type": r.market_type} for r in positions],
        "portfolio_snapshots": [{"id": r.id, "created_at": r.created_at.isoformat() if r.created_at else None,
            "environment_mode": r.environment_mode, "market_type": r.market_type,
            "cash_balance": r.cash_balance, "equity_estimate": r.equity_estimate,
            "unrealized_pnl": r.unrealized_pnl, "realized_pnl": r.realized_pnl,
            "fees_total": r.fees_total, "exposure_notional": r.exposure_notional,
            "positions_open": r.positions_open, "details": r.details} for r in snapshots],
    }


@router.post("/chat")
def chat(payload: ChatRequest, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    # Grounded, rule-based assistant over TITAN system data (adapter-ready for external AI providers later).
    msg = (payload.message or "").strip()
    low = msg.lower()
    state = core_service.get_state(db)
    decisions = db.execute(select(DecisionRecord).order_by(desc(DecisionRecord.id)).limit(5)).scalars().all()
    from app.knowledge.base import list_concepts
    if not msg:
        return {"reply": "اكتب سؤالك عن حالة النظام، القرارات، الاستراتيجيات، المخاطر، أو التحليل.", "grounded": True}
    if any(k in low for k in ["حالة", "status", "يعمل", "متوقف", "demo", "live", "spot", "futures", "earn"]):
        reply = f"حالة النظام الحالية: البيئة={state['environment_mode']}، السوق={state['market_type']}، مستقل={state['autonomous_enabled']}، إيقاف طوارئ={state['emergency_stop']}. التحويل التلقائي Demo→Live معطل وصلاحيات السحب غير مدعومة."
    elif any(k in low for k in ["قرار", "decision", "لماذا", "why", "صفقة", "trade", "دخل", "enter"]):
        if decisions:
            d = decisions[0]
            reply = f"آخر قرار مسجل: {d.symbol} → {d.action} (استراتيجية: {d.strategy}, ثقة: {d.confidence}, مصدر بيانات: {d.data_source}). السبب: {d.rationale}"
        else:
            reply = "لا توجد قرارات مسجلة بعد. يمكنك تشغيل تحليل من Market Scanner."
    elif any(k in low for k in ["استرات", "strategy", "strategies"]):
        sts = core_service.strategies()
        reply = "الاستراتيجيات المسجلة: " + ", ".join([f"{s['name']}[{s['status']}]" for s in sts[:8]]) + ". الحالات تعكس التنفيذ/التكامل الحالي بصدق."
    elif any(k in low for k in ["مخاطر", "risk", "رافعة", "leverage", "رسوم", "fees"]):
        rl = state.get("risk_limits", {})
        reply = f"حدود الأمان الحالية (قبعات إعدادات): {rl}. Spot بلا رافعة؛ Futures تخضع لحدود الرافعة/المخاطر؛ الرسوم والانزلاق تقديرات قابلة للتحقق قبل التنفيذ الحقيقي."
    elif any(k in low for k in ["bos", "choch", "سيولة", "liquidity", "اختراق", "breakout", "شمعة", "candle", "rsi", "macd"]):
        concepts = list_concepts(query=msg)[:3]
        if concepts:
            reply = "مفاهيم ذات صلة من قاعدة المعرفة: " + " | ".join([f"{c['title']}: {c['summary']}" for c in concepts])
        else:
            reply = "يمكنني شرح مفاهيم التداول من قاعدة المعرفة، لكن لم أجد تطابقًا مباشرًا. جرّب: BOS, CHOCH, liquidity, breakout, RSI, MACD."
    elif any(k in low for k in ["افحص", "scan", "حلل", "analyze", "btc", "eth", "usdt"]):
        reply = "لتشغيل تحليل متعدد الأطر (1D/4H/1H/15M/5M) استخدم Market Scanner أو endpoint `api/analysis/decision`. في البناء الحالي البيانات Synthetic/Labeled ما لم يُربط مزود بيانات حقيقي."
    else:
        reply = "أنا مساعد TITAN مرتبط ببيانات النظام. اسألني عن: حالة النظام، آخر القرارات، الاستراتيجيات، المخاطر، أو مفاهيم مثل BOS/CHOCH/السيولة/الاختراق. (التكامل مع مزود LLM خارجي اختياري ويحتاج إعدادًا وآلية أمان.)"
    return {"reply": reply, "grounded": True, "used_external_llm": False, "state_snapshot": {"environment_mode": state["environment_mode"], "market_type": state["market_type"]}}


# --- AI Providers (multi-model, secure refs; no direct trade execution by LLM) ---

class AIProviderCreate(BaseModel):
    name: str
    provider: str
    model: str
    base_url: str | None = None
    api_key_ref: str | None = None  # e.g. env:OPENAI_API_KEY
    enabled: bool = False
    priority: int = 100
    tasks: list[str] | None = None
    daily_budget_usd: float | None = None
    monthly_budget_usd: float | None = None


@router.get("/ai/local/status")
def ai_local_status(_user=Depends(get_current_user)):
    from app.integrations.local_ai import local_model_status
    return {
        **local_model_status(),
        "note": "No API key is required. Install and run a local inference server/model separately; TITAN will not download large model weights or use a cloud fallback.",
    }


@router.post("/ai/local/test")
def ai_local_test(_user=Depends(get_current_user)):
    from app.integrations.local_ai import invoke_local_model
    result = invoke_local_model([
        {"role": "system", "content": "You are a read-only TITAN adviser. You cannot issue or execute trading orders."},
        {"role": "user", "content": "Reply exactly: TITAN_LOCAL_OK"},
    ], timeout=30)
    return result


@router.get("/ai/providers")
def ai_providers(db: Session = Depends(get_db), _user=Depends(get_current_user)):
    rows = list_configs(db)
    return {"supported_providers": sorted(SUPPORTED_PROVIDERS), "items": [
        {"id": r.id, "name": r.name, "provider": r.provider, "model": r.model, "base_url": r.base_url,
         "api_key_ref_set": bool(r.api_key_ref), "enabled": r.enabled, "priority": r.priority, "tasks": r.tasks or [],
         "daily_budget_usd": r.daily_budget_usd, "monthly_budget_usd": r.monthly_budget_usd}
        for r in rows
    ], "safety": "LLM outputs cannot execute trades directly; they must pass market/risk/execution engines. API keys are never logged."}


@router.post("/ai/providers")
def ai_provider_create(payload: AIProviderCreate, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    if payload.provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider. Supported: {sorted(SUPPORTED_PROVIDERS)}")
    row = AIProviderConfig(name=payload.name, provider=payload.provider, model=payload.model, base_url=payload.base_url,
                           api_key_ref=payload.api_key_ref, enabled=payload.enabled, priority=payload.priority,
                           tasks=payload.tasks or [], daily_budget_usd=payload.daily_budget_usd, monthly_budget_usd=payload.monthly_budget_usd)
    db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "name": row.name, "enabled": row.enabled}


@router.post("/ai/providers/{provider_id}/test")
def ai_provider_test(provider_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    cfg = db.get(AIProviderConfig, provider_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="provider not found")
    result = invoke_provider(cfg, [{"role": "user", "content": "Reply with: TITAN_OK"}], task="test")
    record_usage(db, cfg, "test", result)
    db.commit()
    return result.to_dict()


@router.get("/ai/research")
def ai_research():
    # Retrieved during this session via web tools; verify current terms/pricing/access before relying on them.
    return {
        "checked_at_note": "Web lookups performed in-session; provider terms/pricing/availability change. Not live-tested without API keys.",
        "sources": [
            {"provider": "Anthropic", "urls": ["https://www.anthropic.com/legal/supported-regions", "https://platform.claude.com/docs/en/about-claude/pricing"], "algeria_note": "Retrieved supported-regions page listed Algeria; verify directly before use."},
            {"provider": "OpenAI", "urls": ["https://developers.openai.com/api/docs/supported-countries.md", "https://openai.com/api/pricing/"], "algeria_note": "Retrieved supported-countries markdown included Algeria; verify pricing/models/terms directly."},
            {"provider": "Google Gemini API", "urls": ["https://ai.google.dev/gemini-api/docs/available-regions", "https://ai.google.dev/gemini-api/docs/pricing"], "algeria_note": "Could not conclusively confirm Algeria from retrieved content; verify official available-regions page directly."},
            {"provider": "Mistral", "urls": ["https://mistral.ai/pricing", "https://docs.mistral.ai/getting-started/models/"], "algeria_note": "Verify API availability/terms for Algeria directly."},
            {"provider": "DeepSeek", "urls": ["https://api-docs.deepseek.com/quick_start/pricing"], "algeria_note": "Verify availability/terms for Algeria directly."},
            {"provider": "xAI", "urls": ["https://docs.x.ai/developers/models"], "algeria_note": "Verify availability/terms for Algeria directly."},
        ],
        "caveat": "Do not treat any model as connected or feature as working without valid credentials and successful tests. Trading decisions must remain grounded in market data, analysis, and risk engines.",
    }


# --- Compliance (Binance/policy safety gate) ---

@router.post("/compliance/check-order")
def compliance_check_order(payload: AnalyzeRequest, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    # Reuses request shape for quick checks; real execution requires exchange adapter + reconciliation.
    from app.core.enums import MarketType
    g = PreTradeOrderGateway()
    r = g.validate(symbol=payload.symbol, market=MarketType(payload.market_type), side="buy", order_type="limit",
                   quantity=0.001, price=1.0, reference_price=1.0, environment_mode=payload.environment_mode,
                   live_enabled=False)
    from app.services.audit import log_event
    if not r.ok:
        log_event(db, action="compliance.block", severity="warning", message=r.reason, details=r.normalized,
                  environment_mode=payload.environment_mode, market_type=payload.market_type)
        db.commit()
    return {"gateway": r.to_dict(), "sources": ["https://www.binance.com/en/terms", "https://www.binance.com/en/terms-api", "https://developers.binance.com/"],
            "note": "Software precheck only. Verify current official Binance terms/policies, account eligibility, KYC, geo restrictions, symbol filters, and API limits before any live operation."}
