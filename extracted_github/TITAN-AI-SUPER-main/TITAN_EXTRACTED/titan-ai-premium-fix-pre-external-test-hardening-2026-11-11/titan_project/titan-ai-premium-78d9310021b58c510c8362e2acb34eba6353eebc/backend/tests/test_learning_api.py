import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import db as dbmod
from app.api.routes import (
    StrategyApproval,
    StrategyProposalCreate,
    StrategyProposalTest,
    StrategyProposalWalkForward,
    StrategyVersionCreate,
    apply_strategy_proposal_to_paper,
    create_strategy_proposal,
    create_strategy_version,
    get_active_strategy_version,
    list_strategy_proposals,
    list_strategy_versions,
    rollback_strategy_version,
    test_strategy_proposal as api_test_strategy_proposal,
    validate_strategy_proposal_walk_forward,
)


def _db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'learning_api.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    monkeypatch.setattr(dbmod, "engine", engine)
    dbmod.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return factory(), engine


def test_strategy_version_api_persists_lists_activates_and_rolls_back(tmp_path, monkeypatch):
    db, engine = _db(tmp_path, monkeypatch)
    try:
        with pytest.raises(HTTPException) as denied:
            create_strategy_version(
                "trend_following_ema",
                StrategyVersionCreate(params={"ema": 21}, metrics={"expectancy": 0.1},
                                      make_active=True),
                db, None,
            )
        assert denied.value.status_code == 400

        created = create_strategy_version(
            "trend_following_ema",
            StrategyVersionCreate(params={"ema": 20}, metrics={"expectancy": 0.1}),
            db, None,
        )
        assert created["version"] == 1
        created_active = create_strategy_version(
            "trend_following_ema",
            StrategyVersionCreate(params={"ema": 21}, metrics={"expectancy": 0.2},
                                  make_active=True, explicit_approval=True),
            db, None,
        )
        assert created_active["active"] is True
        assert get_active_strategy_version("trend_following_ema", db, None)["active_version"] == 2
        assert len(list_strategy_versions("trend_following_ema", db, None)["items"]) == 2

        rolled = rollback_strategy_version(
            "trend_following_ema", StrategyApproval(explicit_approval=True),
            version=1, db=db, _user=None,
        )
        assert rolled["active_version"] == 1
        assert rolled["live_applied"] is False
    finally:
        db.close()
        engine.dispose()


def test_strategy_proposal_api_requires_paper_test_and_explicit_apply(tmp_path, monkeypatch):
    db, engine = _db(tmp_path, monkeypatch)
    try:
        proposal = create_strategy_proposal(
            "momentum_macd_rsi",
            StrategyProposalCreate(
                current_params={"rsi": 14}, proposed_params={"rsi": 12},
                rationale="Paper test improved expectancy without increasing drawdown",
                metrics_before={"expectancy": 0.1, "max_drawdown": 0.2},
            ),
            db, None,
        )
        proposal_id = proposal["proposal_id"]
        assert list_strategy_proposals("momentum_macd_rsi", db, None)["items"][0]["id"] == proposal_id

        with pytest.raises(HTTPException) as denied:
            apply_strategy_proposal_to_paper(proposal_id, StrategyApproval(explicit_approval=True), db, None)
        assert denied.value.status_code == 400

        tested = api_test_strategy_proposal(
            proposal_id, StrategyProposalTest(
                metrics_after_paper={"expectancy": 0.15, "max_drawdown": 0.18},
            ), db, None,
        )
        assert tested["test_passed"] is True
        validated = validate_strategy_proposal_walk_forward(
            proposal_id,
            StrategyProposalWalkForward(
                baseline_folds=[{"expectancy": 0.1, "max_drawdown": 0.2, "sample_count": 40}] * 2
                    + [{"expectancy": 0.1, "max_drawdown": 0.2, "sample_count": 40}],
                candidate_folds=[{"expectancy": 0.2, "max_drawdown": 0.18, "sample_count": 40}] * 3,
            ),
            db, None,
        )
        assert validated["walk_forward_validated"] is True

        with pytest.raises(HTTPException) as no_approval:
            apply_strategy_proposal_to_paper(proposal_id, StrategyApproval(), db, None)
        assert no_approval.value.status_code == 400

        applied = apply_strategy_proposal_to_paper(
            proposal_id, StrategyApproval(explicit_approval=True), db, None,
        )
        assert applied["ok"] is True
        assert applied["live_applied"] is False
    finally:
        db.close()
        engine.dispose()


def test_strategy_proposal_api_rejects_bad_test_and_does_not_apply_it(tmp_path, monkeypatch):
    db, engine = _db(tmp_path, monkeypatch)
    try:
        proposal = create_strategy_proposal(
            "mean_reversion_bbands",
            StrategyProposalCreate(
                current_params={}, proposed_params={"period": 18},
                rationale="Invalid metrics must not activate a proposal",
                metrics_before={"expectancy": 0.1, "max_drawdown": 0.2},
            ),
            db, None,
        )
        with pytest.raises(HTTPException) as invalid:
            api_test_strategy_proposal(
                proposal["proposal_id"],
                StrategyProposalTest(metrics_after_paper={"expectancy": float("nan")}),
                db, None,
            )
        assert invalid.value.status_code == 400
        with pytest.raises(HTTPException) as blocked:
            apply_strategy_proposal_to_paper(
                proposal["proposal_id"], StrategyApproval(explicit_approval=True), db, None,
            )
        assert blocked.value.status_code == 400
    finally:
        db.close()
        engine.dispose()

