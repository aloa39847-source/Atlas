from pathlib import Path
import sys

root = Path(sys.argv[1])
p = root / "app/learning/continuous.py"
s = p.read_text()

start = s.index("    def test_proposal(")
end = s.index("\n    def approve_for_live(", start)
replacement = '''    def test_proposal(self, proposal_id: int, metrics_after_paper: dict) -> dict[str, Any]:
        for p in self.proposals:
            if p["id"] == proposal_id:
                metrics = metrics_after_paper if isinstance(metrics_after_paper, dict) else {}
                before = p.get("metrics_before", {})
                failures = []
                if int(metrics.get("paper_trading_days", 0) or 0) < 30:
                    failures.append("minimum_30_paper_days_required")
                if int(metrics.get("sample_size", 0) or 0) < 100:
                    failures.append("minimum_100_closed_trades_required")
                if metrics.get("out_of_sample") is not True:
                    failures.append("out_of_sample_validation_required")
                if metrics.get("walk_forward") is not True:
                    failures.append("walk_forward_validation_required")
                if metrics.get("no_lookahead") is not True:
                    failures.append("no_lookahead_evidence_required")
                if metrics.get("net_after_fees") is not True:
                    failures.append("net_after_fees_validation_required")
                if metrics.get("source_verified") is not True:
                    failures.append("verified_data_source_required")
                if int(metrics.get("risk_breaches", 0) or 0) != 0:
                    failures.append("risk_breaches_must_be_zero")
                try:
                    if float(metrics.get("profit_factor", 0)) < 1.1:
                        failures.append("profit_factor_below_1_1")
                    if float(metrics.get("max_drawdown", 1)) > 0.15:
                        failures.append("max_drawdown_above_15_percent")
                    if float(metrics.get("expectancy", 0)) <= float(before.get("expectancy", 0)):
                        failures.append("expectancy_did_not_improve")
                except (TypeError, ValueError):
                    failures.append("invalid_performance_metrics")
                p["tested"] = True
                p["metrics_after_paper"] = metrics
                p["validation_failures"] = failures
                p["test_passed"] = not failures
                p["status"] = "tested_passed" if not failures else "tested_failed"
                self.audit_log.append({"action": "proposal_tested", "proposal_id": proposal_id,
                                       "passed": p["test_passed"], "failures": failures})
                return p
        return {"error": "not_found"}
'''
s = s[:start] + replacement + s[end:]

old = '''                if not p.get("tested"):
                    return {"ok": False, "error": "must_test_first"}
                p["approved_for_live"] = True
'''
new = '''                if not p.get("tested"):
                    return {"ok": False, "error": "must_test_first"}
                if not p.get("test_passed"):
                    return {"ok": False, "error": "all_validation_gates_must_pass", "failures": p.get("validation_failures", [])}
                p["approved_for_live"] = True
'''
if new not in s:
    if old not in s:
        raise SystemExit("Live approval gate marker missing")
    s = s.replace(old, new, 1)

old = '''    def rollback(self, strategy_key: str, to_version: int) -> dict[str, Any]:
        self.audit_log.append({"action": "rollback", "strategy_key": strategy_key, "to_version": to_version})
        return {"ok": True, "rolled_back_to": to_version, "auditable": True}
'''
new = '''    def rollback(self, strategy_key: str, to_version: int) -> dict[str, Any]:
        self.audit_log.append({"action": "rollback_requested", "strategy_key": strategy_key, "to_version": to_version,
                               "applied": False})
        return {"ok": False, "error": "rollback_not_applied_version_store_required",
                "requested_version": to_version, "applied": False, "auditable": True}
'''
if new not in s:
    if old not in s:
        raise SystemExit("Rollback honesty marker missing")
    s = s.replace(old, new, 1)
p.write_text(s)

TESTS = r'''from app.learning.continuous import ContinuousLearningSystem


def _metrics(**overrides):
    data = {
        "paper_trading_days": 45,
        "sample_size": 150,
        "out_of_sample": True,
        "walk_forward": True,
        "no_lookahead": True,
        "net_after_fees": True,
        "source_verified": True,
        "risk_breaches": 0,
        "profit_factor": 1.4,
        "max_drawdown": 0.10,
        "expectancy": 2.0,
    }
    data.update(overrides)
    return data


def test_expectancy_improvement_alone_cannot_pass_learning_gate():
    learner = ContinuousLearningSystem()
    proposal = learner.propose_improvement("ema", {}, {"period": 21}, "test", {"expectancy": 1.0})
    result = learner.test_proposal(proposal["id"], {"expectancy": 2.0})
    assert result["test_passed"] is False
    assert "minimum_30_paper_days_required" in result["validation_failures"]


def test_learning_gate_requires_zero_risk_breaches_and_verified_source():
    learner = ContinuousLearningSystem()
    proposal = learner.propose_improvement("ema", {}, {"period": 21}, "test", {"expectancy": 1.0})
    result = learner.test_proposal(proposal["id"], _metrics(risk_breaches=1, source_verified=False))
    assert result["test_passed"] is False
    assert "risk_breaches_must_be_zero" in result["validation_failures"]
    assert "verified_data_source_required" in result["validation_failures"]


def test_live_approval_requires_successful_validation_and_explicit_approval():
    learner = ContinuousLearningSystem()
    proposal = learner.propose_improvement("ema", {}, {"period": 21}, "test", {"expectancy": 1.0})
    learner.test_proposal(proposal["id"], _metrics())
    assert learner.approve_for_live(proposal["id"], explicit_approval=False)["error"] == "explicit_approval_required"
    approved = learner.approve_for_live(proposal["id"], explicit_approval=True)
    assert approved["ok"] is True


def test_failed_validation_cannot_be_approved_for_live():
    learner = ContinuousLearningSystem()
    proposal = learner.propose_improvement("ema", {}, {"period": 21}, "test", {"expectancy": 1.0})
    learner.test_proposal(proposal["id"], _metrics(max_drawdown=0.40))
    result = learner.approve_for_live(proposal["id"], explicit_approval=True)
    assert result["ok"] is False
    assert result["error"] == "all_validation_gates_must_pass"


def test_rollback_does_not_claim_to_apply_without_version_store():
    learner = ContinuousLearningSystem()
    result = learner.rollback("ema", 1)
    assert result["ok"] is False
    assert result["applied"] is False
'''
(root / "tests/test_continuous_learning_safety.py").write_text(TESTS)
