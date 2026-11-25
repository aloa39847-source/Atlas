from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import db as dbmod
from app.learning.persistent import PersistentStrategyLearning


def _db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'persistent_learning.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    monkeypatch.setattr(dbmod, "engine", engine)
    dbmod.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return factory(), factory, engine


def _pass_walk_forward(service, db, proposal_id):
    baseline = [{"expectancy": 0.1, "max_drawdown": 0.2, "sample_count": 40}] * 3
    candidate = [{"expectancy": 0.2, "max_drawdown": 0.1, "sample_count": 40}] * 3
    return service.validate_walk_forward(db, proposal_id, baseline, candidate)


def test_persistent_strategy_versions_require_approval_for_activation_and_rollback(tmp_path, monkeypatch):
    db, factory, engine = _db(tmp_path, monkeypatch)
    try:
        service = PersistentStrategyLearning()
        first = service.create_version(db, "trend_following_ema", {"fast": 20}, {"expectancy": 0.1})
        assert first["ok"] is True and first["version"] == 1
        denied = service.create_version(
            db, "trend_following_ema", {"fast": 21}, {"expectancy": 0.2}, make_active=True,
        )
        assert denied["error"] == "explicit_approval_required_to_activate_paper_version"
        string_approval = service.create_version(
            db, "trend_following_ema", {"fast": 22}, {"expectancy": 0.3},
            make_active=True, explicit_approval="true",
        )
        assert string_approval["error"] == "explicit_approval_required_to_activate_paper_version"
        second = service.create_version(
            db, "trend_following_ema", {"fast": 21}, {"expectancy": 0.2},
            make_active=True, explicit_approval=True,
        )
        assert second["version"] == 2
        assert service.get_active_version(db, "trend_following_ema")["active_version"] == 2

        assert service.rollback(db, "trend_following_ema", 1)["error"] == "explicit_approval_required"
        assert service.rollback(db, "trend_following_ema", 1, explicit_approval="true")["error"] == "explicit_approval_required"
        rolled = service.rollback(db, "trend_following_ema", 1, explicit_approval=True)
        assert rolled["ok"] is True
        assert rolled["active_version"] == 1
        assert rolled["live_applied"] is False

        db.close()
        reopened = factory()
        try:
            active = service.get_active_version(reopened, "trend_following_ema")
            assert active["active_version"] == 1
            assert active["params"] == {"fast": 20}
        finally:
            reopened.close()
    finally:
        db.close()
        engine.dispose()


def test_persistent_strategy_proposal_requires_paper_test_and_explicit_apply(tmp_path, monkeypatch):
    db, factory, engine = _db(tmp_path, monkeypatch)
    try:
        service = PersistentStrategyLearning()
        proposal = service.create_proposal(
            db, "momentum_macd_rsi",
            {"rsi_period": 14}, {"rsi_period": 12},
            "Research candidate after paper comparison",
            {"expectancy": 0.2, "max_drawdown": 0.12},
        )
        proposal_id = proposal["proposal_id"]
        assert service.apply_proposal_to_paper(db, proposal_id, explicit_approval=True)["error"] == "successful_walk_forward_validation_required"

        tested = service.test_proposal(
            db, proposal_id,
            {"expectancy": 0.25, "max_drawdown": 0.10},
        )
        assert tested["test_passed"] is True
        assert _pass_walk_forward(service, db, proposal_id)["walk_forward_validated"] is True
        assert service.apply_proposal_to_paper(db, proposal_id)["error"] == "explicit_approval_required"
        applied = service.apply_proposal_to_paper(db, proposal_id, explicit_approval=True)
        assert applied["ok"] is True
        assert applied["version"] == 1
        assert applied["live_applied"] is False
        duplicate = service.apply_proposal_to_paper(db, proposal_id, explicit_approval=True)
        assert duplicate == {"ok": False, "error": "proposal_already_applied_to_paper"}
        active = service.get_active_version(db, "momentum_macd_rsi")
        assert active["active_version"] == 1
        assert active["params"] == {"rsi_period": 12}
    finally:
        db.close()
        engine.dispose()


def test_persistent_learning_rejects_non_finite_or_invalid_paper_metrics(tmp_path, monkeypatch):
    db, _, engine = _db(tmp_path, monkeypatch)
    try:
        service = PersistentStrategyLearning()
        proposal = service.create_proposal(
            db, "trend_following_ema", {}, {"period": 21}, "test",
            {"expectancy": 0.1, "max_drawdown": 0.1},
        )
        bad = service.test_proposal(db, proposal["proposal_id"], {"expectancy": float("nan")})
        assert bad["error"] == "non_finite_paper_metrics"
    finally:
        db.close()
        engine.dispose()



def test_persistent_invalid_retest_revokes_previous_pass_and_approval(tmp_path, monkeypatch):
    db, _, engine = _db(tmp_path, monkeypatch)
    try:
        service = PersistentStrategyLearning()
        proposal = service.create_proposal(
            db, "trend_following_ema", {"fast": 10}, {"fast": 12},
            "invalid retest must revoke old pass",
            {"expectancy": 0.1, "max_drawdown": 0.2},
        )
        proposal_id = proposal["proposal_id"]
        assert service.test_proposal(
            db, proposal_id, {"expectancy": 0.2, "max_drawdown": 0.1},
        )["test_passed"] is True
        assert _pass_walk_forward(service, db, proposal_id)["walk_forward_validated"] is True
        assert service.approve_for_live(
            db, proposal_id, explicit_approval=True,
        )["ok"] is True

        invalid = service.test_proposal(
            db, proposal_id, {"expectancy": float("nan"), "max_drawdown": 0.1},
        )
        assert invalid["error"] == "non_finite_paper_metrics"
        assert service.apply_proposal_to_paper(
            db, proposal_id, explicit_approval=True,
        )["error"] == "successful_walk_forward_validation_required"
        assert service.approve_for_live(
            db, proposal_id, explicit_approval=True,
        )["error"] == "successful_walk_forward_validation_required"
    finally:
        db.close()
        engine.dispose()


def test_persistent_proposal_approval_requires_literal_boolean(tmp_path, monkeypatch):
    db, _, engine = _db(tmp_path, monkeypatch)
    try:
        service = PersistentStrategyLearning()
        proposal = service.create_proposal(
            db, "trend_following_ema", {}, {"period": 21}, "literal approval",
            {"expectancy": 0.1, "max_drawdown": 0.2},
        )
        proposal_id = proposal["proposal_id"]
        assert service.approve_for_live(
            db, proposal_id, explicit_approval="true",
        )["error"] == "explicit_approval_required"
        assert service.apply_proposal_to_paper(
            db, proposal_id, explicit_approval="true",
        )["error"] == "explicit_approval_required"
    finally:
        db.close()
        engine.dispose()


def test_persistent_learning_rejects_all_invalid_drawdown_and_expectancy_inputs(tmp_path, monkeypatch):
    db, _, engine = _db(tmp_path, monkeypatch)
    try:
        service = PersistentStrategyLearning()
        proposal = service.create_proposal(
            db, "trend_following_ema", {}, {"period": 21}, "invalid metrics",
            {"expectancy": 0.1, "max_drawdown": 0.2},
        )
        proposal_id = proposal["proposal_id"]
        invalid_cases = [
            ({"expectancy": float("inf")}, "non_finite_paper_metrics"),
            ({"expectancy": "not-a-number"}, "invalid_paper_metrics"),
            ({"expectancy": 0.2, "max_drawdown": -0.1}, "negative_drawdown_not_allowed"),
        ]
        for metrics, expected_error in invalid_cases:
            result = service.test_proposal(db, proposal_id, metrics)
            assert result["error"] == expected_error

        bad_baselines = [
            ({"expectancy": float("nan"), "max_drawdown": 0.2}, {"expectancy": 0.2}),
            ({"expectancy": 0.1, "max_drawdown": -0.1}, {"expectancy": 0.2}),
        ]
        for before, after in bad_baselines:
            candidate = service.create_proposal(
                db, "trend_following_ema", {}, {"period": 22}, "invalid baseline", before,
            )
            result = service.test_proposal(db, candidate["proposal_id"], after)
            expected = "non_finite_paper_metrics" if before["expectancy"] != 0.1 else "negative_drawdown_not_allowed"
            assert result["error"] == expected
    finally:
        db.close()
        engine.dispose()


def test_persistent_paper_apply_rolls_back_all_records_on_commit_failure(tmp_path, monkeypatch):
    db, _, engine = _db(tmp_path, monkeypatch)
    try:
        service = PersistentStrategyLearning()
        proposal = service.create_proposal(
            db, "trend_following_ema", {"fast": 10}, {"fast": 12},
            "atomic apply", {"expectancy": 0.1, "max_drawdown": 0.2},
        )
        proposal_id = proposal["proposal_id"]
        assert service.test_proposal(
            db, proposal_id, {"expectancy": 0.2, "max_drawdown": 0.1},
        )["test_passed"] is True
        assert _pass_walk_forward(service, db, proposal_id)["walk_forward_validated"] is True

        original_commit = db.commit
        def fail_commit():
            raise RuntimeError("simulated commit failure")
        db.commit = fail_commit
        try:
            service.apply_proposal_to_paper(db, proposal_id, explicit_approval=True)
            assert False, "commit failure should propagate"
        except RuntimeError as exc:
            assert "simulated commit failure" in str(exc)
        finally:
            db.commit = original_commit

        db.expire_all()
        assert service.list_versions(db, "trend_following_ema") == []
        assert service.get_active_version(db, "trend_following_ema")["ok"] is False
        stored = db.get(__import__("app.models.entities", fromlist=["LearningRecord"]).LearningRecord, proposal_id)
        assert stored.evidence["applied_to_paper"] is False
    finally:
        db.close()
        engine.dispose()


def test_persistent_learning_rejects_boolean_paper_metrics(tmp_path, monkeypatch):
    db, factory, engine = _db(tmp_path, monkeypatch)
    try:
        service = PersistentStrategyLearning()
        proposal = service.create_proposal(
            db, "momentum_macd_rsi", {"rsi": 14}, {"rsi": 12},
            "Boolean metrics must not pass", {"expectancy": 0.1, "max_drawdown": 0.1},
        )
        db.commit()
        result = service.test_proposal(
            db, proposal["proposal_id"], {"expectancy": True, "max_drawdown": 0.1},
        )
        assert result["error"] == "boolean_paper_metric_not_allowed"
    finally:
        db.close()
        engine.dispose()



def test_persistent_walk_forward_requires_samples_and_final_holdout_improvement(tmp_path, monkeypatch):
    db, _, engine = _db(tmp_path, monkeypatch)
    try:
        service = PersistentStrategyLearning()
        proposal = service.create_proposal(
            db, "trend_following_ema", {"fast": 9}, {"fast": 10},
            "walk-forward test", {"expectancy": 0.1, "max_drawdown": 0.2},
        )
        proposal_id = proposal["proposal_id"]
        baseline = [{"expectancy": 0.1, "max_drawdown": 0.2, "sample_count": 40}] * 3
        small = [{"expectancy": 0.2, "max_drawdown": 0.1, "sample_count": 10}] * 3
        assert service.validate_walk_forward(db, proposal_id, baseline, small)["error"] == "walk_forward_fold_below_quality_gate_0"
        candidate = [
            {"expectancy": 0.3, "max_drawdown": 0.1, "sample_count": 40},
            {"expectancy": 0.3, "max_drawdown": 0.1, "sample_count": 40},
            {"expectancy": 0.05, "max_drawdown": 0.1, "sample_count": 40},
        ]
        failed = service.validate_walk_forward(db, proposal_id, baseline, candidate)
        assert failed["walk_forward_validated"] is False
        assert service.apply_proposal_to_paper(db, proposal_id, explicit_approval=True)["error"] == "successful_walk_forward_validation_required"
    finally:
        db.close()
        engine.dispose()

def test_persistent_walk_forward_final_holdout_cannot_mask_validation_regression(tmp_path, monkeypatch):
    db, _, engine = _db(tmp_path, monkeypatch)
    try:
        service = PersistentStrategyLearning()
        proposal = service.create_proposal(
            db, "trend_following_ema", {"fast": 9}, {"fast": 10},
            "holdout isolation", {"expectancy": 0.1, "max_drawdown": 0.2},
        )
        baseline = [{"expectancy": 0.1, "max_drawdown": 0.2, "sample_count": 40}] * 3
        # The excellent final holdout would inflate an aggregate that incorrectly
        # includes it, masking regression on both walk-forward validation folds.
        candidate = [
            {"expectancy": 0.05, "max_drawdown": 0.1, "sample_count": 40},
            {"expectancy": 0.05, "max_drawdown": 0.1, "sample_count": 40},
            {"expectancy": 1.0, "max_drawdown": 0.1, "sample_count": 40},
        ]
        result = service.validate_walk_forward(db, proposal["proposal_id"], baseline, candidate)
        assert result["walk_forward_validated"] is False
        assert result["walk_forward"]["candidate_expectancy"] == 0.05
        assert result["walk_forward"]["baseline_expectancy"] == 0.1
        assert result["walk_forward"]["folds"] == 2
        assert result["walk_forward"]["total_folds"] == 3
        assert result["walk_forward"]["holdout_sample_count"] == 40
    finally:
        db.close()
        engine.dispose()


def test_persistent_walk_forward_rejects_fractional_sample_counts(tmp_path, monkeypatch):
    db, _, engine = _db(tmp_path, monkeypatch)
    try:
        service = PersistentStrategyLearning()
        proposal = service.create_proposal(
            db, "trend_following_ema", {}, {"fast": 10}, "integer samples only",
            {"expectancy": 0.1, "max_drawdown": 0.2},
        )
        baseline = [{"expectancy": 0.1, "max_drawdown": 0.2, "sample_count": 40}] * 3
        candidate = [{"expectancy": 0.2, "max_drawdown": 0.1, "sample_count": 40} for _ in range(3)]
        candidate[1]["sample_count"] = 40.5
        result = service.validate_walk_forward(db, proposal["proposal_id"], baseline, candidate)
        assert result["error"] == "invalid_walk_forward_fold_1"
    finally:
        db.close()
        engine.dispose()

