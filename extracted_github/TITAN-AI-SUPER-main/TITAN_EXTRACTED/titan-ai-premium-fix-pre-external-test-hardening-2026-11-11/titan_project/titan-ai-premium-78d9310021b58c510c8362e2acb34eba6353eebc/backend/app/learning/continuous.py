"""Continuous learning proposals with auditable, reversible strategy versions.

This module is an in-memory research/paper-learning component. It never applies
parameters to a live trading engine and never authorizes exchange execution.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
from typing import Any


class StrategyVersion:
    def __init__(self, strategy_key: str, version: int, params: dict, metrics: dict):
        self.strategy_key = strategy_key
        self.version = version
        self.params = deepcopy(params)
        self.metrics = deepcopy(metrics)
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.approved_for_live = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_key": self.strategy_key,
            "version": self.version,
            "params": deepcopy(self.params),
            "metrics": deepcopy(self.metrics),
            "created_at": self.created_at,
            "approved_for_live": self.approved_for_live,
        }


class ContinuousLearningSystem:
    def __init__(self):
        self.versions: dict[str, list[StrategyVersion]] = {}
        self.active_versions: dict[str, int] = {}
        self.proposals: list[dict[str, Any]] = []
        self.audit_log: list[dict[str, Any]] = []

    def register_version(
        self,
        strategy_key: str,
        params: dict[str, Any],
        metrics: dict[str, Any],
        *,
        make_active: bool = False,
        explicit_approval: bool = False,
    ) -> dict[str, Any]:
        key = str(strategy_key or "").strip()
        if not key or not isinstance(params, dict) or not isinstance(metrics, dict):
            return {"ok": False, "error": "strategy_key_params_and_metrics_required"}
        if make_active and explicit_approval is not True:
            return {"ok": False, "error": "explicit_approval_required_to_activate_paper_version"}
        history = self.versions.setdefault(key, [])
        version = StrategyVersion(key, len(history) + 1, params, metrics)
        history.append(version)
        if make_active:
            self.active_versions[key] = version.version
        self.audit_log.append({
            "action": "strategy_version_registered",
            "strategy_key": key,
            "version": version.version,
            "active_version": self.active_versions.get(key),
            "ts": version.created_at,
        })
        return {
            "ok": True, "version": version.to_dict(),
            "active_version": self.active_versions.get(key),
            "active": self.active_versions.get(key) == version.version,
        }

    def get_active_version(self, strategy_key: str) -> dict[str, Any]:
        key = str(strategy_key or "").strip()
        active = self.active_versions.get(key)
        version = next((v for v in self.versions.get(key, []) if v.version == active), None)
        if version is None:
            return {"ok": False, "error": "active_strategy_version_not_found"}
        return {"ok": True, "version": version.to_dict()}

    def list_versions(self, strategy_key: str) -> list[dict[str, Any]]:
        return [version.to_dict() for version in self.versions.get(strategy_key, [])]

    def propose_improvement(
        self,
        strategy_key: str,
        current_params: dict,
        proposed_params: dict,
        rationale: str,
        metrics_before: dict,
    ) -> dict[str, Any]:
        if not strategy_key or not isinstance(current_params, dict) or not isinstance(proposed_params, dict) or not isinstance(metrics_before, dict) or not str(rationale).strip():
            return {"ok": False, "error": "valid_strategy_params_rationale_and_metrics_required"}
        proposal = {
            "id": len(self.proposals) + 1,
            "strategy_key": strategy_key,
            "current_params": deepcopy(current_params),
            "proposed_params": deepcopy(proposed_params),
            "rationale": str(rationale).strip(),
            "metrics_before": deepcopy(metrics_before),
            "status": "proposed",
            "tested": False,
            "test_passed": False,
            "approved_for_live": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.proposals.append(proposal)
        self.audit_log.append({"action": "proposal_created", "proposal_id": proposal["id"], "ts": proposal["created_at"]})
        return deepcopy(proposal)

    def test_proposal(self, proposal_id: int, metrics_after_paper: dict) -> dict[str, Any]:
        for proposal in self.proposals:
            if proposal["id"] == proposal_id:
                # A new test attempt invalidates any earlier pass/approval until it
                # has been revalidated. Invalid input must never leave stale success.
                proposal["tested"] = False
                proposal["test_passed"] = False
                proposal["approved_for_live"] = False
                proposal["walk_forward_validated"] = False
                proposal.pop("walk_forward", None)
                proposal.pop("metrics_after_paper", None)
                proposal["status"] = "paper_test_invalid"
                metrics_before = proposal.get("metrics_before")
                if (not isinstance(metrics_before, dict) or not isinstance(metrics_after_paper, dict)
                        or "expectancy" not in metrics_after_paper):
                    return {"ok": False, "error": "paper_metrics_with_expectancy_required"}
                if any(isinstance(metrics.get(key), bool) for metrics in (
                    metrics_before, metrics_after_paper,
                ) for key in ("expectancy", "max_drawdown")):
                    return {"ok": False, "error": "boolean_paper_metric_not_allowed"}
                try:
                    before = float(metrics_before.get("expectancy", 0.0))
                    after = float(metrics_after_paper.get("expectancy", 0.0))
                    before_dd = float(metrics_before.get("max_drawdown", 0.0))
                    after_dd = float(metrics_after_paper.get("max_drawdown", before_dd))
                except (TypeError, ValueError, OverflowError):
                    return {"ok": False, "error": "invalid_paper_metrics"}
                numeric_metrics = (before, after, before_dd, after_dd)
                if not all(math.isfinite(value) for value in numeric_metrics):
                    return {"ok": False, "error": "non_finite_paper_metrics"}
                if before_dd < 0 or after_dd < 0:
                    return {"ok": False, "error": "negative_drawdown_not_allowed"}
                proposal["tested"] = True
                proposal["metrics_after_paper"] = deepcopy(metrics_after_paper)
                proposal["test_passed"] = after > before and after_dd <= before_dd
                proposal["status"] = "paper_test_passed" if proposal["test_passed"] else "paper_test_failed"
                self.audit_log.append({
                    "action": "proposal_tested",
                    "proposal_id": proposal_id,
                    "passed": proposal["test_passed"],
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
                return deepcopy(proposal)
        return {"ok": False, "error": "not_found"}

    def validate_walk_forward(
        self,
        proposal_id: int,
        baseline_folds: list[dict[str, Any]],
        candidate_folds: list[dict[str, Any]],
        *,
        min_folds: int = 3,
        min_samples_per_fold: int = 30,
    ) -> dict[str, Any]:
        """Validate candidate parameters over chronological walk-forward folds.

        Each fold contains expectancy, max_drawdown and sample_count. The final
        fold is treated as an untouched holdout. Passing this method creates a
        research validation record only; it never changes active/live parameters.
        """
        proposal = next((item for item in self.proposals if item["id"] == proposal_id), None)
        if proposal is None:
            return {"ok": False, "error": "not_found"}
        proposal["walk_forward_validated"] = False
        if (not isinstance(baseline_folds, list) or not isinstance(candidate_folds, list)
                or isinstance(min_folds, bool) or not isinstance(min_folds, int) or min_folds < 3
                or isinstance(min_samples_per_fold, bool) or not isinstance(min_samples_per_fold, int)
                or min_samples_per_fold < 10 or len(baseline_folds) != len(candidate_folds)
                or len(baseline_folds) < min_folds):
            return {"ok": False, "error": "insufficient_or_mismatched_walk_forward_folds"}
        parsed = []
        for index, (baseline, candidate) in enumerate(zip(baseline_folds, candidate_folds)):
            values = []
            for row in (baseline, candidate):
                if not isinstance(row, dict) or any(isinstance(row.get(key), bool) for key in (
                    "expectancy", "max_drawdown", "sample_count"
                )):
                    return {"ok": False, "error": f"invalid_walk_forward_fold_{index}"}
                try:
                    expectancy = float(row["expectancy"])
                    drawdown = float(row["max_drawdown"])
                    samples = int(row["sample_count"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    return {"ok": False, "error": f"invalid_walk_forward_fold_{index}"}
                if (not math.isfinite(expectancy) or not math.isfinite(drawdown)
                        or drawdown < 0 or samples < min_samples_per_fold):
                    return {"ok": False, "error": f"walk_forward_fold_below_quality_gate_{index}"}
                values.append({"expectancy": expectancy, "max_drawdown": drawdown, "sample_count": samples})
            parsed.append((values[0], values[1]))
        baseline_mean = sum(pair[0]["expectancy"] for pair in parsed) / len(parsed)
        candidate_mean = sum(pair[1]["expectancy"] for pair in parsed) / len(parsed)
        baseline_dd = sum(pair[0]["max_drawdown"] for pair in parsed) / len(parsed)
        candidate_dd = sum(pair[1]["max_drawdown"] for pair in parsed) / len(parsed)
        holdout_baseline, holdout_candidate = parsed[-1]
        no_fold_drawdown_regression = all(
            candidate["max_drawdown"] <= baseline["max_drawdown"]
            for baseline, candidate in parsed
        )
        passed = (
            candidate_mean > baseline_mean
            and candidate_dd <= baseline_dd
            and no_fold_drawdown_regression
            and holdout_candidate["expectancy"] > holdout_baseline["expectancy"]
        )
        proposal["walk_forward_validated"] = passed
        proposal["tested"] = True
        proposal["test_passed"] = passed
        proposal["walk_forward"] = {
            "folds": len(parsed), "baseline_expectancy": baseline_mean,
            "candidate_expectancy": candidate_mean, "baseline_avg_drawdown": baseline_dd,
            "candidate_avg_drawdown": candidate_dd,
            "holdout_expectancy_improved": holdout_candidate["expectancy"] > holdout_baseline["expectancy"],
            "all_fold_drawdowns_non_worsening": no_fold_drawdown_regression,
            "minimum_samples_per_fold": min_samples_per_fold,
            "research_only": True,
        }
        proposal["status"] = "walk_forward_passed" if passed else "walk_forward_failed"
        self.audit_log.append({
            "action": "walk_forward_validation", "proposal_id": proposal_id,
            "passed": passed, "ts": datetime.now(timezone.utc).isoformat(),
        })
        return deepcopy(proposal)

    def approve_for_live(self, proposal_id: int, explicit_approval: bool = False) -> dict[str, Any]:
        if explicit_approval is not True:
            return {"ok": False, "error": "explicit_approval_required", "note": "Changes cannot be applied to live without explicit approval"}
        for proposal in self.proposals:
            if proposal["id"] == proposal_id:
                if not proposal.get("tested") or not proposal.get("test_passed") or not proposal.get("walk_forward_validated"):
                    return {"ok": False, "error": "successful_walk_forward_validation_required"}
                proposal["approved_for_live"] = True
                proposal["status"] = "approved_pending_separate_live_integration"
                self.audit_log.append({
                    "action": "approved_for_live",
                    "proposal_id": proposal_id,
                    "explicit": True,
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
                return {"ok": True, "proposal_id": proposal_id, "applied": False}
        return {"ok": False, "error": "not_found"}

    def apply_proposal_to_paper(
        self, proposal_id: int, *, explicit_approval: bool = False,
    ) -> dict[str, Any]:
        if explicit_approval is not True:
            return {"ok": False, "error": "explicit_approval_required"}
        for proposal in self.proposals:
            if proposal["id"] != proposal_id:
                continue
            if proposal.get("applied_to_paper") is True:
                return {"ok": False, "error": "proposal_already_applied_to_paper"}
            if not proposal.get("tested") or not proposal.get("test_passed") or not proposal.get("walk_forward_validated"):
                return {"ok": False, "error": "successful_walk_forward_validation_required"}
            metrics = proposal.get("metrics_after_paper") or {}
            created = self.register_version(
                proposal["strategy_key"], proposal["proposed_params"], metrics,
                make_active=True, explicit_approval=True,
            )
            if not created.get("ok"):
                return created
            proposal["applied_to_paper"] = True
            proposal["status"] = "applied_to_paper_version_store"
            proposal["applied_version"] = created["version"]["version"]
            self.audit_log.append({
                "action": "proposal_applied_to_paper",
                "proposal_id": proposal_id,
                "version": proposal["applied_version"],
                "ts": datetime.now(timezone.utc).isoformat(),
            })
            return {"ok": True, "proposal_id": proposal_id, "version": created["version"], "live_applied": False}
        return {"ok": False, "error": "not_found"}

    def rollback(
        self, strategy_key: str, to_version: int, *, explicit_approval: bool = False,
    ) -> dict[str, Any]:
        if explicit_approval is not True:
            return {"ok": False, "error": "explicit_approval_required"}
        key = str(strategy_key or "").strip()
        version = next((v for v in self.versions.get(key, []) if v.version == to_version), None)
        if version is None:
            return {"ok": False, "error": "strategy_version_not_found", "strategy_key": key, "requested_version": to_version}
        previous = self.active_versions.get(key)
        self.active_versions[key] = version.version
        self.audit_log.append({
            "action": "strategy_version_rollback",
            "strategy_key": key,
            "from_version": previous,
            "to_version": version.version,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        return {
            "ok": True,
            "strategy_key": key,
            "active_version": version.version,
            "params": deepcopy(version.params),
            "metrics": deepcopy(version.metrics),
            "in_memory_only": True,
            "note": "Version pointer restored in this process only; no live strategy or exchange configuration was changed.",
        }

    def export_state(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot for a future durable store."""
        return {
            "versions": {key: [version.to_dict() for version in rows] for key, rows in self.versions.items()},
            "active_versions": deepcopy(self.active_versions),
            "proposals": deepcopy(self.proposals),
            "audit_log": deepcopy(self.audit_log),
        }
