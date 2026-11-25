from app.learning.continuous import ContinuousLearningSystem


def test_strategy_versions_apply_to_paper_and_rollback_to_real_version():
    learning = ContinuousLearningSystem()
    first = learning.register_version(
        "trend_following_ema",
        {"fast": 10, "slow": 30},
        {"expectancy": 0.1, "max_drawdown": 0.2},
        make_active=True, explicit_approval=True,
    )
    assert first["ok"] is True
    assert first["active_version"] == 1

    proposal = learning.propose_improvement(
        "trend_following_ema",
        {"fast": 10, "slow": 30},
        {"fast": 12, "slow": 36},
        "Improved paper expectancy with lower drawdown.",
        {"expectancy": 0.1, "max_drawdown": 0.2},
    )
    assert proposal["status"] == "proposed"
    tested = learning.test_proposal(
        proposal["id"],
        {"expectancy": 0.2, "max_drawdown": 0.15, "sample_count": 100},
    )
    assert tested["test_passed"] is True
    folds = [{"expectancy": 0.1, "max_drawdown": 0.2, "sample_count": 40}] * 2 + [{"expectancy": 0.1, "max_drawdown": 0.2, "sample_count": 40}]
    candidate_folds = [{"expectancy": 0.15, "max_drawdown": 0.18, "sample_count": 40}] * 3
    assert learning.validate_walk_forward(proposal["id"], folds, candidate_folds)["walk_forward_validated"] is True
    assert learning.approve_for_live(proposal["id"])["error"] == "explicit_approval_required"
    approved = learning.approve_for_live(proposal["id"], explicit_approval=True)
    assert approved["ok"] is True
    assert approved["applied"] is False

    applied = learning.apply_proposal_to_paper(proposal["id"], explicit_approval=True)
    assert applied["ok"] is True
    assert applied["live_applied"] is False
    duplicate = learning.apply_proposal_to_paper(proposal["id"], explicit_approval=True)
    assert duplicate == {"ok": False, "error": "proposal_already_applied_to_paper"}
    assert learning.get_active_version("trend_following_ema")["version"]["version"] == 2

    assert learning.rollback("trend_following_ema", 1)["error"] == "explicit_approval_required"
    rolled_back = learning.rollback("trend_following_ema", 1, explicit_approval=True)
    assert rolled_back["ok"] is True
    assert rolled_back["active_version"] == 1
    assert rolled_back["params"] == {"fast": 10, "slow": 30}
    assert rolled_back["in_memory_only"] is True


def test_learning_rejects_failed_paper_proposal_and_unknown_rollback():
    learning = ContinuousLearningSystem()
    proposal = learning.propose_improvement(
        "momentum_macd_rsi", {"rsi": 14}, {"rsi": 12},
        "Test proposal", {"expectancy": 0.2, "max_drawdown": 0.1},
    )
    failed = learning.test_proposal(
        proposal["id"], {"expectancy": 0.1, "max_drawdown": 0.2},
    )
    assert failed["test_passed"] is False
    assert learning.apply_proposal_to_paper(
        proposal["id"], explicit_approval=True,
    )["error"] == "successful_walk_forward_validation_required"
    assert learning.rollback("missing_strategy", 1, explicit_approval=True)["error"] == "strategy_version_not_found"


def test_learning_proposal_requires_valid_rationale_and_paper_metrics():
    learning = ContinuousLearningSystem()
    invalid = learning.propose_improvement("x", {}, {}, "", {})
    assert invalid["ok"] is False
    proposal = learning.propose_improvement("x", {"a": 1}, {"a": 2}, "valid", {"expectancy": 0})
    assert learning.test_proposal(proposal["id"], {})["error"] == "paper_metrics_with_expectancy_required"


def test_learning_rejects_non_finite_or_invalid_paper_metrics():
    learning = ContinuousLearningSystem()
    proposal = learning.propose_improvement(
        "trend_following_ema", {"fast": 10}, {"fast": 12},
        "finite metric validation", {"expectancy": 0.1, "max_drawdown": 0.2},
    )
    assert learning.test_proposal(proposal["id"], {"expectancy": float("inf")})["error"] == "non_finite_paper_metrics"
    assert learning.test_proposal(proposal["id"], {"expectancy": "not-a-number"})["error"] == "invalid_paper_metrics"
    assert learning.test_proposal(proposal["id"], {"expectancy": 0.2, "max_drawdown": -0.1})["error"] == "negative_drawdown_not_allowed"
    assert proposal["status"] == "proposed"


def test_learning_requires_literal_boolean_for_sensitive_approval():
    learning = ContinuousLearningSystem()
    proposal = learning.propose_improvement(
        "trend_following_ema", {"fast": 10}, {"fast": 12},
        "approval validation", {"expectancy": 0.1, "max_drawdown": 0.2},
    )
    learning.test_proposal(proposal["id"], {"expectancy": 0.2, "max_drawdown": 0.1})
    assert learning.approve_for_live(proposal["id"], explicit_approval="true")["error"] == "explicit_approval_required"
    assert learning.apply_proposal_to_paper(proposal["id"], explicit_approval="true")["error"] == "explicit_approval_required"



def test_invalid_retest_revokes_previous_paper_pass_and_live_approval():
    learning = ContinuousLearningSystem()
    proposal = learning.propose_improvement(
        "trend_following_ema", {"fast": 10}, {"fast": 12},
        "stale pass must not survive invalid retest",
        {"expectancy": 0.1, "max_drawdown": 0.2},
    )
    passed = learning.test_proposal(
        proposal["id"], {"expectancy": 0.2, "max_drawdown": 0.1},
    )
    assert passed["test_passed"] is True
    baseline = [{"expectancy": 0.1, "max_drawdown": 0.2, "sample_count": 40}] * 3
    candidate = [{"expectancy": 0.2, "max_drawdown": 0.1, "sample_count": 40}] * 3
    assert learning.validate_walk_forward(proposal["id"], baseline, candidate)["walk_forward_validated"] is True
    assert learning.approve_for_live(
        proposal["id"], explicit_approval=True,
    )["ok"] is True

    invalid = learning.test_proposal(
        proposal["id"], {"expectancy": float("nan"), "max_drawdown": 0.1},
    )
    assert invalid["error"] == "non_finite_paper_metrics"
    assert learning.apply_proposal_to_paper(
        proposal["id"], explicit_approval=True,
    )["error"] == "successful_walk_forward_validation_required"
    assert learning.approve_for_live(
        proposal["id"], explicit_approval=True,
     )["error"] == "successful_walk_forward_validation_required"


def test_in_memory_paper_version_activation_requires_literal_approval():
    learning = ContinuousLearningSystem()
    inactive = learning.register_version("trend_following_ema", {"fast": 10}, {"expectancy": 0.1})
    assert inactive["ok"] is True
    assert inactive["active"] is False
    assert inactive["active_version"] is None
    assert learning.get_active_version("trend_following_ema")["ok"] is False

    denied = learning.register_version(
        "trend_following_ema", {"fast": 12}, {"expectancy": 0.2},
        make_active=True, explicit_approval="true",
    )
    assert denied["error"] == "explicit_approval_required_to_activate_paper_version"

    approved = learning.register_version(
        "trend_following_ema", {"fast": 12}, {"expectancy": 0.2},
        make_active=True, explicit_approval=True,
    )
    assert approved["active"] is True
    assert approved["active_version"] == 2
    assert learning.rollback("trend_following_ema", 1, explicit_approval="true")["error"] == "explicit_approval_required"


def test_continuous_learning_rejects_boolean_paper_metrics():
    learning = ContinuousLearningSystem()
    proposal = learning.propose_improvement(
        "momentum_macd_rsi", {"rsi": 14}, {"rsi": 12},
        "Boolean metrics must not pass", {"expectancy": 0.1, "max_drawdown": 0.1},
    )
    result = learning.test_proposal(
        proposal["id"], {"expectancy": True, "max_drawdown": 0.1},
    )
    assert result["error"] == "boolean_paper_metric_not_allowed"
    assert proposal["test_passed"] is False



def test_walk_forward_requires_minimum_samples_and_unimproved_holdout_fails():
    learning = ContinuousLearningSystem()
    proposal = learning.propose_improvement(
        "trend_following_ema", {"fast": 10}, {"fast": 12},
        "walk-forward gate", {"expectancy": 0.1, "max_drawdown": 0.2},
    )
    baseline = [{"expectancy": 0.1, "max_drawdown": 0.2, "sample_count": 40}] * 3
    too_small = [{"expectancy": 0.2, "max_drawdown": 0.1, "sample_count": 10}] * 3
    assert learning.validate_walk_forward(proposal["id"], baseline, too_small)["error"] == "walk_forward_fold_below_quality_gate_0"
    candidate = [
        {"expectancy": 0.3, "max_drawdown": 0.1, "sample_count": 40},
        {"expectancy": 0.3, "max_drawdown": 0.1, "sample_count": 40},
        {"expectancy": 0.05, "max_drawdown": 0.1, "sample_count": 40},
    ]
    failed = learning.validate_walk_forward(proposal["id"], baseline, candidate)
    assert failed["walk_forward_validated"] is False
    assert failed["status"] == "walk_forward_failed"
    assert learning.apply_proposal_to_paper(proposal["id"], explicit_approval=True)["error"] == "successful_walk_forward_validation_required"
