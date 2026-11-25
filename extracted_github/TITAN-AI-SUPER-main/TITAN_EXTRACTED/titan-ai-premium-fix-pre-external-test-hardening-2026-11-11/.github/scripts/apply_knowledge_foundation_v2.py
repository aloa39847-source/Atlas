from pathlib import Path
import sys

root = Path(sys.argv[1])

# Add the persistent concept model.
p = root / "app/models/entities.py"
s = p.read_text()
if "class KnowledgeConceptRecord(Base):" not in s:
    marker = "class LearningRecord(Base):\n"
    model = '''class KnowledgeConceptRecord(Base):
    __tablename__ = "knowledge_concepts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(256))
    summary: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    caution: Mapped[str] = mapped_column(Text, default="")
    confidence_note: Mapped[str] = mapped_column(Text, default="Pending source verification and testing.")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)


'''
    if marker not in s:
        raise SystemExit("KnowledgeConceptRecord insertion marker missing")
    s = s.replace(marker, model + marker, 1)
    p.write_text(s)

# Source-linked knowledge registry and coverage.
p = root / "app/knowledge/base.py"
s = p.read_text()
if "def coverage_report(db=None)" not in s:
    s = s.replace("from dataclasses import asdict, dataclass\n",
                  "from dataclasses import asdict, dataclass, field\n", 1)
    s = s.replace(
        '    confidence_note: str = "Educational/system knowledge entry. Validate in current market context and with tested data."\n',
        '    confidence_note: str = "Educational/system knowledge entry. Validate in current market context and with tested data."\n'
        '    source_keys: list[str] = field(default_factory=list)\n'
        '    verified: bool = False\n'
        '    confidence: float = 0.0\n',
        1,
    )
    bottom = r'''REQUIRED_KNOWLEDGE_DOMAINS: dict[str, dict[str, Any]] = {
    "market_structure": {"title": "Market structure and trend", "categories": ["market_structure"]},
    "candlesticks": {"title": "Candlesticks and price action", "categories": ["candlesticks", "price_action"]},
    "technical_indicators": {"title": "Technical indicators and signal math", "categories": ["indicators"]},
    "chart_patterns": {"title": "Chart patterns and breakouts", "categories": ["price_action", "candlesticks"]},
    "volume": {"title": "Volume analysis", "categories": ["volume"]},
    "order_flow": {"title": "Order flow and delta", "categories": ["order_flow", "volume"]},
    "liquidity": {"title": "Liquidity and execution depth", "categories": ["liquidity"]},
    "market_microstructure": {"title": "Market microstructure and spread", "categories": ["microstructure"]},
    "spot_market": {"title": "Spot market mechanics", "categories": ["spot_market"]},
    "futures_market": {"title": "Futures and perpetual contracts", "categories": ["futures_market"]},
    "leverage_margin": {"title": "Leverage and margin mechanics", "categories": ["leverage_margin", "risk"]},
    "liquidation": {"title": "Liquidation and margin calls", "categories": ["liquidation", "risk"]},
    "funding": {"title": "Funding and basis", "categories": ["funding", "execution"]},
    "order_types": {"title": "Order types and matching", "categories": ["order_types", "execution"]},
    "execution": {"title": "Execution quality and slippage", "categories": ["execution"]},
    "risk_management": {"title": "Trade and portfolio risk", "categories": ["risk"]},
    "portfolio_management": {"title": "Portfolio allocation and exposure", "categories": ["portfolio"]},
    "backtesting": {"title": "Backtesting methodology", "categories": ["backtesting"]},
    "walk_forward_validation": {"title": "Walk-forward and out-of-sample validation", "categories": ["validation", "backtesting"]},
    "statistics": {"title": "Statistics and probability", "categories": ["statistics"]},
    "time_series": {"title": "Time-series analysis", "categories": ["time_series", "statistics"]},
    "market_regimes": {"title": "Market regimes and volatility states", "categories": ["regimes", "strategies"]},
    "strategy_design": {"title": "Strategy design and robustness", "categories": ["strategies"]},
    "machine_learning": {"title": "Machine learning and model validation", "categories": ["machine_learning"]},
    "data_quality": {"title": "Data quality and provenance", "categories": ["data_quality", "operations"]},
    "exchange_api": {"title": "Exchange APIs and reconciliation", "categories": ["exchange_api", "operations"]},
    "security": {"title": "API security and secrets", "categories": ["security", "operations"]},
    "compliance": {"title": "Compliance and operational constraints", "categories": ["compliance", "operations"]},
    "macro_economics": {"title": "Macro and cross-asset context", "categories": ["macro"]},
    "correlation": {"title": "Correlation and cross-market risk", "categories": ["correlation", "analysis"]},
    "on_chain": {"title": "On-chain data and limitations", "categories": ["on_chain"]},
    "tokenomics": {"title": "Tokenomics and supply mechanics", "categories": ["tokenomics"]},
    "stablecoins": {"title": "Stablecoins and depeg risk", "categories": ["stablecoins"]},
    "trading_psychology": {"title": "Trading psychology and behavioral bias", "categories": ["psychology"]},
    "operations": {"title": "Monitoring, incidents and recovery", "categories": ["operations"]},
}


def _knowledge_records(db=None):
    records = [asdict(x) for x in SEED_CONCEPTS]
    if db is not None:
        from sqlalchemy import select
        from app.models.entities import KnowledgeConceptRecord
        dynamic = db.execute(select(KnowledgeConceptRecord).order_by(KnowledgeConceptRecord.category, KnowledgeConceptRecord.key)).scalars().all()
        records.extend({
            "key": x.key, "category": x.category, "title": x.title, "summary": x.summary,
            "details": x.details or [], "tags": x.tags or [], "caution": x.caution,
            "confidence_note": x.confidence_note, "source_keys": [], "verified": False,
            "confidence": float(x.confidence or 0.0),
        } for x in dynamic)
    return records


def _source_links(db=None):
    links: dict[str, set[int]] = {}
    if db is not None:
        from sqlalchemy import select
        from app.models.entities import KnowledgeSource
        rows = db.execute(select(KnowledgeSource).where(
            KnowledgeSource.verified.is_(True), KnowledgeSource.reliability_score >= 0.7
        )).scalars().all()
        for source in rows:
            metadata = source.metadata_json or {}
            if metadata.get("verification_method") != "manual_admin_review":
                continue
            for key in metadata.get("concept_keys", []):
                links.setdefault(str(key), set()).add(int(source.id))
    return links


def list_concepts(category: str | None = None, query: str | None = None, db=None) -> list[dict[str, Any]]:
    items = _knowledge_records(db)
    links = _source_links(db)
    for item in items:
        ids = sorted(links.get(item["key"], set()))
        item["source_keys"] = [str(x) for x in ids]
        item["verified"] = len(ids) >= 2
        if ids:
            item["confidence"] = max(float(item.get("confidence", 0.0)), 0.7)
    if category:
        c = category.strip().lower()
        items = [x for x in items if x["category"].lower() == c]
    if query:
        q = query.strip().lower()
        items = [x for x in items if q in x["title"].lower() or q in x["summary"].lower() or q in " ".join(x["tags"]).lower() or q in " ".join(x["details"]).lower()]
    return items


def categories(db=None) -> list[str]:
    return sorted({x["category"] for x in _knowledge_records(db)})


def coverage_report(db=None) -> dict[str, Any]:
    """Report verified knowledge coverage; unverified seed notes never count as training-ready."""
    concepts = _knowledge_records(db)
    source_links = _source_links(db)
    manual_source_ids = set().union(*(ids for ids in source_links.values())) if source_links else set()
    domain_rows = []
    ready_count = 0
    for domain, spec in REQUIRED_KNOWLEDGE_DOMAINS.items():
        concepts_in_domain = [c for c in concepts if c["category"] in spec["categories"] or c["category"] == domain]
        verified_concepts = [c for c in concepts_in_domain if len(source_links.get(c["key"], set())) >= 2]
        source_ids = set().union(*(source_links.get(c["key"], set()) for c in concepts_in_domain)) if concepts_in_domain else set()
        ready = len(verified_concepts) >= 3 and len(source_ids) >= 2
        ready_count += int(ready)
        domain_rows.append({
            "key": domain, "title": spec["title"], "seeded_concepts": len(concepts_in_domain),
            "verified_concepts": len(verified_concepts), "verified_sources": len(source_ids),
            "ready": ready,
        })
    missing = [r["key"] for r in domain_rows if not r["ready"]]
    verified_concepts_total = sum(1 for c in concepts if len(source_links.get(c["key"], set())) >= 2)
    return {
        "foundation_ready": not missing,
        "coverage_percent": round(ready_count / len(domain_rows) * 100, 1) if domain_rows else 0.0,
        "required_domains": len(domain_rows), "ready_domains": ready_count,
        "seeded_concepts": len(concepts), "verified_concepts": verified_concepts_total,
        "verified_sources": len(manual_source_ids), "missing_domains": missing,
        "domains": domain_rows,
        "promotion_blocked_until_ready": True,
        "note": "Only manually admin-verified sources linked to concepts count. Seed summaries are educational notes, not a complete verified training corpus.",
    }
'''
    s = s[:s.index("def list_concepts(")] + bottom
    p.write_text(s)

# FastAPI routes and models.
p = root / "app/api/routes.py"
s = p.read_text()
if "class KnowledgeConceptCreate(BaseModel):" not in s:
    s = s.replace("from typing import Any\n", "from typing import Any\nfrom datetime import datetime, timezone\n", 1)
    s = s.replace(
        "from app.models.entities import AIProviderConfig, AuditLog, ConfigChange, DecisionRecord, KnowledgeSource, LearningRecord",
        "from app.models.entities import AIProviderConfig, AuditLog, ConfigChange, DecisionRecord, KnowledgeSource, KnowledgeConceptRecord, LearningRecord",
        1,
    )
    s = s.replace(
        '''class KnowledgeCreate(BaseModel):
    title: str
    source_type: str = "manual"
    url: str | None = None
    reliability_score: float | None = Field(None, ge=0, le=1)
    verified: bool = False
    notes: str = ""
''',
        '''class KnowledgeCreate(BaseModel):
    title: str
    source_type: str = "manual"
    url: str | None = None
    reliability_score: float | None = Field(None, ge=0, le=1)
    verified: bool = False  # ignored on create; only admin verification may set this
    notes: str = ""
    concept_keys: list[str] = Field(default_factory=list)


class KnowledgeVerify(BaseModel):
    concept_keys: list[str] = Field(..., min_length=1)
    reliability_score: float = Field(..., ge=0.7, le=1.0)
    verification_notes: str = Field(..., min_length=20)


class KnowledgeConceptCreate(BaseModel):
    key: str = Field(..., min_length=3, max_length=128, pattern=r"^[a-z0-9_]+$")
    category: str = Field(..., min_length=2, max_length=64)
    title: str = Field(..., min_length=3, max_length=256)
    summary: str = Field(..., min_length=20)
    details: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    caution: str = ""
    confidence_note: str = "Pending source verification and testing."
''',
        1,
    )
    auth_start = s.index("def _bearer(")
    auth_end = s.index("\n\n@router.", auth_start)
    auth_block = s[auth_start:auth_end]
    s = s[:auth_start] + s[auth_end:]
    insert = s.index("\n\n@router.get(")
    s = s[:insert] + "\n\n" + auth_block + s[insert:]

    s = s.replace(
        'return {"items": [{"id": r.id, "title": r.title, "source_type": r.source_type, "url": r.url, "reliability_score": r.reliability_score, "verified": r.verified, "notes": r.notes} for r in rows]}',
        'return {"items": [{"id": r.id, "title": r.title, "source_type": r.source_type, "url": r.url, "reliability_score": r.reliability_score, "verified": r.verified, "notes": r.notes, "metadata": r.metadata_json} for r in rows]}',
        1,
    )
    s = s.replace(
        '''    r = KnowledgeSource(title=payload.title, source_type=payload.source_type, url=payload.url, reliability_score=payload.reliability_score, verified=payload.verified, notes=payload.notes)
    db.add(r); db.commit(); db.refresh(r)
    return {"id": r.id, "title": r.title}
''',
        '''    r = KnowledgeSource(title=payload.title, source_type=payload.source_type, url=payload.url,
                        reliability_score=payload.reliability_score, verified=False,
                        notes=(payload.notes + " | Verification pending admin review.").strip(),
                        metadata_json={"concept_keys": payload.concept_keys})
    db.add(r); db.commit(); db.refresh(r)
    return {"id": r.id, "title": r.title, "verified": False}


@router.post("/knowledge/{source_id}/verify")
def verify_knowledge_source(source_id: int, payload: KnowledgeVerify, db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="admin_verification_required")
    source = db.get(KnowledgeSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="knowledge_source_not_found")
    if not source.url or not source.url.startswith("https://"):
        raise HTTPException(status_code=400, detail="https_source_url_required")
    from app.knowledge.base import SEED_CONCEPTS
    dynamic_keys = db.execute(select(KnowledgeConceptRecord.key)).scalars().all()
    valid_keys = {c.key for c in SEED_CONCEPTS} | set(dynamic_keys)
    if any(key not in valid_keys for key in payload.concept_keys):
        raise HTTPException(status_code=400, detail="unknown_concept_key")
    source.verified = True
    source.reliability_score = payload.reliability_score
    source.notes = payload.verification_notes
    source.metadata_json = {"concept_keys": payload.concept_keys, "verification_method": "manual_admin_review",
                            "verified_by": getattr(user, "id", None), "verified_at": datetime.now(timezone.utc).isoformat()}
    db.add(source); db.commit()
    return {"ok": True, "id": source.id, "verified": True}
''',
        1,
    )
    s = s.replace(
        '''def knowledge_concepts(category: str | None = Query(None), q: str | None = Query(None)):
    from app.knowledge.base import categories, list_concepts
    return {"categories": categories(), "items": list_concepts(category=category, query=q)}
''',
        '''def knowledge_concepts(category: str | None = Query(None), q: str | None = Query(None), db: Session = Depends(get_db)):
    from app.knowledge.base import categories, list_concepts, coverage_report
    return {"categories": categories(db), "items": list_concepts(category=category, query=q, db=db), "coverage": coverage_report(db)}


@router.post("/knowledge/concepts")
def add_knowledge_concept(payload: KnowledgeConceptCreate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="admin_concept_creation_required")
    from app.knowledge.base import SEED_CONCEPTS
    existing = {c.key for c in SEED_CONCEPTS} | set(db.execute(select(KnowledgeConceptRecord.key)).scalars().all())
    if payload.key in existing:
        raise HTTPException(status_code=409, detail="knowledge_concept_key_exists")
    row = KnowledgeConceptRecord(key=payload.key, category=payload.category, title=payload.title,
                                  summary=payload.summary, details=payload.details, tags=payload.tags,
                                  caution=payload.caution, confidence_note=payload.confidence_note,
                                  confidence=0.0, metadata_json={"verified": False})
    db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "key": row.key, "verified": False}


@router.get("/knowledge/coverage")
def knowledge_coverage(db: Session = Depends(get_db)):
    from app.knowledge.base import coverage_report
    return coverage_report(db)
''',
        1,
    )
    s = s.replace(
        '''def system_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {
        "state": core_service.get_state(db),
''',
        '''def system_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.knowledge.base import coverage_report
    return {
        "state": core_service.get_state(db),
        "knowledge_foundation": coverage_report(db),
''',
        1,
    )
    p.write_text(s)

# Starlette routes mirror the same source-verification policy.
p = root / "app/star_app.py"
s = p.read_text()
if "async def knowledge_concept_add(" not in s:
    s = s.replace("import json\n", "import json\nfrom datetime import datetime, timezone\n", 1)
    s = s.replace(
        "from app.models.entities import AIProviderConfig, AuditLog, ConfigChange, DecisionRecord, KnowledgeSource, LearningRecord, OrderRecord, PositionRecord, SystemState",
        "from app.models.entities import AIProviderConfig, AuditLog, ConfigChange, DecisionRecord, KnowledgeSource, KnowledgeConceptRecord, LearningRecord, OrderRecord, PositionRecord, SystemState",
        1,
    )
    s = s.replace(
        'return J({"items": [{"id": r.id, "title": r.title, "source_type": r.source_type, "url": r.url, "reliability_score": r.reliability_score, "verified": r.verified, "notes": r.notes} for r in rows]})',
        'return J({"items": [{"id": r.id, "title": r.title, "source_type": r.source_type, "url": r.url, "reliability_score": r.reliability_score, "verified": r.verified, "notes": r.notes, "metadata": r.metadata_json} for r in rows]})',
        1,
    )
    s = s.replace(
        '''        r = KnowledgeSource(title=p.get("title",""), source_type=p.get("source_type","manual"), url=p.get("url"), reliability_score=p.get("reliability_score"), verified=bool(p.get("verified")), notes=p.get("notes",""))
        db.add(r); db.commit(); db.refresh(r)
        return J({"id": r.id, "title": r.title})
''',
        '''        r = KnowledgeSource(title=p.get("title",""), source_type=p.get("source_type","manual"), url=p.get("url"),
                            reliability_score=p.get("reliability_score"), verified=False,
                            notes=(p.get("notes","") + " | Verification pending admin review.").strip(),
                            metadata_json={"concept_keys": p.get("concept_keys", [])})
        db.add(r); db.commit(); db.refresh(r)
        return J({"id": r.id, "title": r.title, "verified": False})
''',
        1,
    )
    marker = "async def knowledge_concepts(request: Request):\n"
    start = s.index(marker)
    end = s.index("\n\nasync def learning_list", start)
    handlers = '''async def knowledge_verify(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        if not getattr(user, "is_admin", False): return J({"error": "admin_verification_required"}, 403)
        source_id = int(request.path_params["source_id"])
        source = db.get(KnowledgeSource, source_id)
        if source is None: return J({"error": "knowledge_source_not_found"}, 404)
        p = await body(request)
        if not (source.url or "").startswith("https://"): return J({"error": "https_source_url_required"}, 400)
        from app.knowledge.base import SEED_CONCEPTS
        keys = p.get("concept_keys", [])
        dynamic_keys = db.execute(select(KnowledgeConceptRecord.key)).scalars().all()
        valid = {c.key for c in SEED_CONCEPTS} | set(dynamic_keys)
        if not keys or any(k not in valid for k in keys): return J({"error": "unknown_or_missing_concept_key"}, 400)
        score = float(p.get("reliability_score", 0))
        notes = str(p.get("verification_notes", ""))
        if score < 0.7 or len(notes) < 20: return J({"error": "verification_evidence_insufficient"}, 400)
        source.verified = True
        source.reliability_score = score
        source.notes = notes
        source.metadata_json = {"concept_keys": keys, "verification_method": "manual_admin_review",
                                "verified_by": getattr(user, "id", None), "verified_at": datetime.now(timezone.utc).isoformat()}
        db.add(source); db.commit()
        return J({"ok": True, "id": source.id, "verified": True})
    finally:
        db.close()


async def knowledge_coverage(request: Request):
    db = db_sess()
    try:
        from app.knowledge.base import coverage_report
        return J(coverage_report(db))
    finally:
        db.close()


async def knowledge_concept_add(request: Request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        if not getattr(user, "is_admin", False): return J({"error": "admin_concept_creation_required"}, 403)
        p = await body(request)
        key = str(p.get("key", ""))
        if len(key) < 3 or len(key) > 128 or not all(c.islower() or c.isdigit() or c == "_" for c in key):
            return J({"error": "invalid_concept_key"}, 400)
        from app.knowledge.base import SEED_CONCEPTS
        existing = {c.key for c in SEED_CONCEPTS} | set(db.execute(select(KnowledgeConceptRecord.key)).scalars().all())
        if key in existing: return J({"error": "knowledge_concept_key_exists"}, 409)
        summary = str(p.get("summary", ""))
        if len(summary) < 20: return J({"error": "concept_summary_too_short"}, 400)
        row = KnowledgeConceptRecord(key=key, category=str(p.get("category", "general")),
            title=str(p.get("title", "")), summary=summary, details=p.get("details", []), tags=p.get("tags", []),
            caution=str(p.get("caution", "")), confidence_note=str(p.get("confidence_note", "Pending source verification and testing.")),
            confidence=0.0, metadata_json={"verified": False})
        db.add(row); db.commit(); db.refresh(row)
        return J({"id": row.id, "key": row.key, "verified": False})
    finally:
        db.close()


async def knowledge_concepts(request: Request):
    db = db_sess()
    try:
        from app.knowledge.base import categories, list_concepts, coverage_report
        return J({"categories": categories(db), "items": list_concepts(category=request.query_params.get("category"), query=request.query_params.get("q"), db=db), "coverage": coverage_report(db)})
    finally:
        db.close()
'''
    s = s[:start] + handlers + s[end:]
    s = s.replace(
        '''        return J({
            "state": core_service.get_state(db),
''',
        '''        from app.knowledge.base import coverage_report
        return J({
            "state": core_service.get_state(db),
            "knowledge_foundation": coverage_report(db),
''',
        1,
    )
    s = s.replace(
        '''    Route("/api/knowledge/concepts", knowledge_concepts),
''',
        '''    Route("/api/knowledge/{source_id:int}/verify", knowledge_verify, methods=["POST"]),
    Route("/api/knowledge/coverage", knowledge_coverage),
    Route("/api/knowledge/concepts", knowledge_concepts),
    Route("/api/knowledge/concepts", knowledge_concept_add, methods=["POST"]),
''',
        1,
    )
    p.write_text(s)

# Gate autonomous start and continuous learning until the knowledge corpus is complete.
p = root / "app/services/core_service.py"
s = p.read_text()
if "master.start_blocked_knowledge_foundation" not in s:
    marker = '        if s.environment_mode == "live" or s.account_target == "live":\n'
    block = '''        from app.knowledge.base import coverage_report
        knowledge_status = coverage_report(db)
        checks["knowledge_foundation_ready"] = knowledge_status["foundation_ready"]
        checks["knowledge_coverage_percent"] = knowledge_status["coverage_percent"]
        if not knowledge_status["foundation_ready"]:
            log_event(db, action="master.start_blocked_knowledge_foundation", severity="warning",
                      message="Autonomous start blocked: verified knowledge foundation is incomplete.",
                      details={"coverage_percent": knowledge_status["coverage_percent"],
                               "missing_domains": knowledge_status["missing_domains"]})
            db.commit()
            raise ValueError("Autonomous start blocked: verified knowledge foundation is incomplete. Manual analysis remains available.")
'''
    if marker not in s:
        raise SystemExit("Core start knowledge gate marker missing")
    s = s.replace(marker, block + marker, 1)
    p.write_text(s)

p = root / "app/learning/continuous.py"
s = p.read_text()
if "knowledge_foundation_ready: bool = False" not in s:
    s = s.replace(
        "    def __init__(self):\n        self.versions = {}\n",
        "    def __init__(self, *, knowledge_foundation_ready: bool = False):\n        self.knowledge_foundation_ready = bool(knowledge_foundation_ready)\n        self.versions = {}\n",
        1,
    )
    marker = "    def propose_improvement(self, strategy_key: str, current_params: dict, proposed_params: dict, rationale: str, metrics_before: dict) -> dict[str, Any]:\n        proposal = {\n"
    new = "    def propose_improvement(self, strategy_key: str, current_params: dict, proposed_params: dict, rationale: str, metrics_before: dict) -> dict[str, Any]:\n        if not self.knowledge_foundation_ready:\n            return {\"ok\": False, \"error\": \"knowledge_foundation_incomplete\", \"proposal_created\": False}\n        proposal = {\n"
    if marker not in s:
        raise SystemExit("Continuous-learning foundation gate marker missing")
    s = s.replace(marker, new, 1)
    p.write_text(s)

# Expose knowledge coverage in both system status responses.
p = root / "app/api/routes.py"
s = p.read_text()
s = s.replace(
    '''def system_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {
        "state": core_service.get_state(db),
''',
    '''def system_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.knowledge.base import coverage_report
    return {
        "state": core_service.get_state(db),
        "knowledge_foundation": coverage_report(db),
''',
    1,
)
p.write_text(s)

# Tests: autonomous start remains blocked, unverified sources do not count, admin source review is required.
p = root / "tests/test_http_starlette.py"
s = p.read_text().replace(
    'assert r.status_code == 200 and r.json()["ok"] is True\n            r = c.post("/api/master/stop"',
    'assert r.status_code == 400\n            assert "knowledge foundation is incomplete" in r.json().get("detail", "")\n            r = c.post("/api/master/stop"',
    1,
)
p.write_text(s)

p = root / "tests/test_continuous_learning_safety.py"
s = p.read_text().replace("ContinuousLearningSystem()", "ContinuousLearningSystem(knowledge_foundation_ready=True)")
safety_test = "def test_continuous_learning_is_disabled_by_default_until_knowledge_foundation_ready():"
if safety_test not in s:
    s += '''

def test_continuous_learning_is_disabled_by_default_until_knowledge_foundation_ready():
    learner = ContinuousLearningSystem()
    result = learner.propose_improvement("ema", {}, {"period": 21}, "test", {"expectancy": 1.0})
    assert result["ok"] is False
    assert result["error"] == "knowledge_foundation_incomplete"
    assert result["proposal_created"] is False
'''
else:
    test_start = s.index(safety_test)
    s = s[:test_start] + s[test_start:].replace(
        "learner = ContinuousLearningSystem(knowledge_foundation_ready=True)",
        "learner = ContinuousLearningSystem()", 1
    )
p.write_text(s)

test_file = root / "tests/test_knowledge_foundation_gates.py"
test_file.write_text(r'''from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.db import Base
from app.models.entities import KnowledgeSource
from app.api.routes import KnowledgeCreate, KnowledgeVerify, KnowledgeConceptCreate, add_knowledge, verify_knowledge_source, add_knowledge_concept
from app.knowledge.base import coverage_report


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'knowledge.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_knowledge_foundation_starts_incomplete_and_reports_gaps(tmp_path):
    db = _db(tmp_path)
    try:
        report = coverage_report(db)
        assert report["foundation_ready"] is False
        assert report["coverage_percent"] == 0.0
        assert report["required_domains"] >= 30
        assert len(report["missing_domains"]) == report["required_domains"]
    finally:
        db.close()


def test_user_cannot_mark_a_new_knowledge_source_verified(tmp_path):
    db = _db(tmp_path)
    try:
        result = add_knowledge(
            KnowledgeCreate(title="Official exchange documentation", source_type="official",
                            url="https://example.org/docs", reliability_score=1.0,
                            verified=True, notes="submitted by user", concept_keys=["market_structure_bos"]),
            db,
            _user=SimpleNamespace(is_admin=False),
        )
        source = db.get(KnowledgeSource, result["id"])
        assert source.verified is False
        assert source.metadata_json["concept_keys"] == ["market_structure_bos"]
    finally:
        db.close()


def test_only_admin_can_manually_verify_a_source_with_evidence(tmp_path):
    db = _db(tmp_path)
    try:
        source = KnowledgeSource(title="Official exchange docs", source_type="official",
                                 url="https://example.org/docs", reliability_score=0.9,
                                 verified=False, metadata_json={"concept_keys": ["market_structure_bos"]})
        db.add(source)
        db.commit()
        db.refresh(source)
        with pytest.raises(HTTPException) as exc:
            verify_knowledge_source(source.id,
                KnowledgeVerify(concept_keys=["market_structure_bos"], reliability_score=0.9,
                                verification_notes="Checked official documentation and confirmed the claims."),
                db, user=SimpleNamespace(is_admin=False))
        assert exc.value.status_code == 403
        result = verify_knowledge_source(source.id,
            KnowledgeVerify(concept_keys=["market_structure_bos"], reliability_score=0.9,
                            verification_notes="Checked official documentation and confirmed the claims."),
            db, user=SimpleNamespace(is_admin=True, id=7))
        assert result["verified"] is True
        stored = db.get(KnowledgeSource, source.id)
        assert stored.verified is True
        assert stored.metadata_json["verification_method"] == "manual_admin_review"
    finally:
        db.close()


def test_unverified_sources_do_not_increase_knowledge_coverage(tmp_path):
    db = _db(tmp_path)
    try:
        db.add(KnowledgeSource(title="Unverified", source_type="manual", url="https://example.org",
                               reliability_score=1.0, verified=False,
                               metadata_json={"concept_keys": ["market_structure_bos"]}))
        db.commit()
        report = coverage_report(db)
        assert report["verified_sources"] == 0
        assert report["verified_concepts"] == 0
        assert report["foundation_ready"] is False
    finally:
        db.close()


def test_admin_can_add_concepts_for_missing_domains(tmp_path):
    db = _db(tmp_path)
    try:
        result = add_knowledge_concept(
            KnowledgeConceptCreate(key="indicators_ema", category="indicators", title="Exponential moving average",
                                  summary="An exponential moving average weights recent prices more heavily than older observations.",
                                  details=["It is a lagging indicator."], tags=["EMA"]),
            db, user=SimpleNamespace(is_admin=True, id=7),
        )
        from app.models.entities import KnowledgeConceptRecord
        record = db.query(KnowledgeConceptRecord).filter_by(key="indicators_ema").one()
        assert result["verified"] is False
        assert record.confidence == 0.0
    finally:
        db.close()


def test_coverage_counts_domain_only_after_three_concepts_and_two_verified_sources(tmp_path):
    db = _db(tmp_path)
    try:
        from app.models.entities import KnowledgeConceptRecord
        for key, title in (("indicators_ema", "EMA"), ("indicators_rsi", "RSI"), ("indicators_macd", "MACD")):
            db.add(KnowledgeConceptRecord(key=key, category="indicators", title=title,
                                          summary=f"Verified educational description for {title} indicator.",
                                          details=[], tags=[title], confidence=0.0))
        db.commit()
        for i in range(2):
            db.add(KnowledgeSource(title=f"Official technical reference {i}", source_type="official",
                url=f"https://example.org/technical-{i}", reliability_score=0.95, verified=True,
                metadata_json={"concept_keys": ["indicators_ema", "indicators_rsi", "indicators_macd"],
                               "verification_method": "manual_admin_review"}))
        db.commit()
        report = coverage_report(db)
        domain = next(x for x in report["domains"] if x["key"] == "technical_indicators")
        assert domain["ready"] is True
        assert domain["verified_concepts"] == 3
        assert domain["verified_sources"] == 2
        assert report["foundation_ready"] is False
    finally:
        db.close()
''')
