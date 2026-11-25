from pathlib import Path
import sys

root = Path(sys.argv[1])
path = root / "app/learning/continuous.py"
s = path.read_text()

if "from math import isfinite" not in s:
    s = s.replace("import json", "from math import isfinite\nimport json", 1)

start = s.index("    def test_proposal(")
end = s.index("    def approve_for_live(", start)
new_method = '''    def test_proposal(self, proposal_id: int, metrics_after_paper: dict) -> dict[str, Any]:
        for p in self.proposals:
            if p["id"] == proposal_id:
                metrics = metrics_after_paper if isinstance(metrics_after_paper, dict) else {}
                before = p.get("metrics_before", {})
                failures = []
                if not isinstance(metrics_after_paper, dict):
                    failures.append("performance_metrics_must_be_object")
                if not isinstance(before, dict):
                    failures.append("baseline_metrics_must_be_object")
                    before = {}

                def finite_number(source: dict, key: str) -> float | None:
                    value = source.get(key)
                    if isinstance(value, bool):
                        return None
                    try:
                        number = float(value)
                    except (TypeError, ValueError, OverflowError):
                        return None
                    return number if isfinite(number) else None

                def whole_count(key: str) -> int | None:
                    number = finite_number(metrics, key)
                    if number is None or number < 0 or not number.is_integer():
                        return None
                    return int(number)

                days = whole_count("paper_trading_days")
                samples = whole_count("sample_size")
                breaches = whole_count("risk_breaches")
                if days is None:
                    failures.append("paper_trading_days_invalid")
                    if "paper_trading_days" not in metrics:
                        failures.append("minimum_30_paper_days_required")
                elif days < 30:
                    failures.append("minimum_30_paper_days_required")
                if samples is None:
                    failures.append("sample_size_invalid")
                    if "sample_size" not in metrics:
                        failures.append("minimum_100_closed_trades_required")
                elif samples < 100:
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
                if breaches is None:
                    failures.append("risk_breach_count_invalid")
                elif breaches != 0:
                    failures.append("risk_breaches_must_be_zero")

                profit_factor = finite_number(metrics, "profit_factor")
                if profit_factor is None:
                    failures.append("profit_factor_invalid")
                elif profit_factor < 1.1:
                    failures.append("profit_factor_below_1_1")

                max_drawdown = finite_number(metrics, "max_drawdown")
                if max_drawdown is None or not 0 <= max_drawdown <= 1:
                    failures.append("max_drawdown_invalid")
                elif max_drawdown > 0.15:
                    failures.append("max_drawdown_above_15_percent")

                expectancy = finite_number(metrics, "expectancy")
                baseline_expectancy = finite_number(before, "expectancy")
                if expectancy is None or baseline_expectancy is None:
                    failures.append("expectancy_metrics_invalid")
                elif expectancy <= baseline_expectancy:
                    failures.append("expectancy_did_not_improve")

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
if "def finite_number(source: dict, key: str)" not in s:
    s = s[:start] + new_method + s[end:]
path.write_text(s)

test_path = root / "tests/test_continuous_learning_safety.py"
t = test_path.read_text()
if "test_learning_gate_rejects_non_finite_metrics" not in t:
    t += '''

def test_learning_gate_rejects_non_finite_metrics():
    import math
    learner = ContinuousLearningSystem(knowledge_foundation_ready=True)
    proposal = learner.propose_improvement("ema", {}, {"period": 21}, "test", {"expectancy": 1.0})
    result = learner.test_proposal(proposal["id"], _metrics(
        profit_factor=math.nan, max_drawdown=math.inf, expectancy=math.nan))
    assert result["test_passed"] is False
    assert "profit_factor_invalid" in result["validation_failures"]
    assert "max_drawdown_invalid" in result["validation_failures"]
    assert "expectancy_metrics_invalid" in result["validation_failures"]


def test_learning_gate_rejects_invalid_counts_and_negative_drawdown():
    learner = ContinuousLearningSystem(knowledge_foundation_ready=True)
    proposal = learner.propose_improvement("ema", {}, {"period": 21}, "test", {"expectancy": 1.0})
    result = learner.test_proposal(proposal["id"], _metrics(
        paper_trading_days=float("nan"), sample_size=100.5,
        risk_breaches=float("inf"), max_drawdown=-0.01))
    assert result["test_passed"] is False
    assert "paper_trading_days_invalid" in result["validation_failures"]
    assert "sample_size_invalid" in result["validation_failures"]
    assert "risk_breach_count_invalid" in result["validation_failures"]
    assert "max_drawdown_invalid" in result["validation_failures"]


def test_learning_gate_rejects_non_object_metrics_without_crashing():
    learner = ContinuousLearningSystem(knowledge_foundation_ready=True)
    proposal = learner.propose_improvement("ema", {}, {"period": 21}, "test", {"expectancy": 1.0})
    result = learner.test_proposal(proposal["id"], [1, 2, 3])
    assert result["test_passed"] is False
    assert "performance_metrics_must_be_object" in result["validation_failures"]
'''
test_path.write_text(t)

print("Continuous-learning metrics now fail closed on malformed and non-finite values.")
