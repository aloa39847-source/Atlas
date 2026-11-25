"""Durable paper-only strategy version and proposal records using LearningRecord."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import LearningRecord


class PersistentStrategyLearning:
    """Persists research versions and approvals; never mutates live strategy config."""

    @staticmethod
    def _latest(db: Session, category: str, subject: str):
        return db.execute(
            select(LearningRecord).where(
                LearningRecord.category == category,
                LearningRecord.subject == subject,
            ).order_by(LearningRecord.id.desc()).limit(1)
        ).scalars().first()

    def list_versions(self, db: Session, strategy_key: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = db.execute(
            select(LearningRecord).where(
                LearningRecord.category == "strategy_version",
                LearningRecord.subject == strategy_key,
            ).order_by(LearningRecord.id.desc()).limit(max(1, min(int(limit), 500)))
        ).scalars().all()
        return [{
            "record_id": row.id, "strategy_key": row.subject,
            "version": (row.evidence or {}).get("version"),
            "params": deepcopy((row.evidence or {}).get("params", {})),
            "metrics": deepcopy((row.evidence or {}).get("metrics", {})),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in rows]

    def create_version(self, db: Session, strategy_key: str, params: dict, metrics: dict, *, make_active: bool = False, explicit_approval: bool = False) -> dict[str, Any]:
        key = str(strategy_key or "").strip()
        if make_active and explicit_approval is not True:
            return {"ok": False, "error": "explicit_approval_required_to_activate_paper_version"}
        if not key or not isinstance(params, dict) or not isinstance(metrics, dict):
            return {"ok": False, "error": "strategy_key_params_and_metrics_required"}
        versions = self.list_versions(db, key, limit=500)
        version = max((int(item.get("version") or 0) for item in versions), default=0) + 1
        record = LearningRecord(
            category="strategy_version", subject=key, outcome=f"version_{version}",
            lesson="Persisted research/paper strategy parameter snapshot; not a live configuration change.",
            evidence={"version": version, "params": deepcopy(params), "metrics": deepcopy(metrics)},
            verified=False,
        )
        db.add(record)
        db.flush()
        if make_active:
            self._set_active_record(db, key, version, record.id, reason="version_created_as_active")
        db.commit()
        db.refresh(record)
        return {"ok": True, "record_id": record.id, "strategy_key": key, "version": version, "active": make_active, "live_applied": False}

    def _set_active_record(self, db: Session, key: str, version: int, version_record_id: int, *, reason: str):
        db.add(LearningRecord(
            category="strategy_active_version", subject=key, outcome=str(version),
            lesson="Paper/research active-version pointer only; no live strategy configuration changed.",
            evidence={"version": version, "version_record_id": version_record_id, "reason": reason},
            verified=False,
        ))

    def get_active_version(self, db: Session, strategy_key: str) -> dict[str, Any]:
        active = self._latest(db, "strategy_active_version", strategy_key)
        if active is None:
            return {"ok": False, "error": "active_strategy_version_not_set"}
        version_record_id = (active.evidence or {}).get("version_record_id")
        record = db.get(LearningRecord, version_record_id) if version_record_id else None
        if record is None:
            return {"ok": False, "error": "active_strategy_version_record_missing"}
        return {
            "ok": True, "strategy_key": strategy_key,
            "active_version": int((record.evidence or {}).get("version", 0)),
            "params": deepcopy((record.evidence or {}).get("params", {})),
            "metrics": deepcopy((record.evidence or {}).get("metrics", {})),
            "live_applied": False,
        }

    def rollback(self, db: Session, strategy_key: str, version: int, *, explicit_approval: bool = False) -> dict[str, Any]:
        if explicit_approval is not True:
            return {"ok": False, "error": "explicit_approval_required"}
        rows = db.execute(select(LearningRecord).where(
            LearningRecord.category == "strategy_version",
            LearningRecord.subject == strategy_key,
        )).scalars().all()
        record = next((row for row in rows if int((row.evidence or {}).get("version", 0)) == int(version)), None)
        if record is None:
            return {"ok": False, "error": "strategy_version_not_found"}
        self._set_active_record(db, strategy_key, int(version), record.id, reason="explicit_paper_rollback")
        db.commit()
        return {
            "ok": True, "strategy_key": strategy_key, "active_version": int(version),
            "params": deepcopy((record.evidence or {}).get("params", {})),
            "live_applied": False, "note": "Only the persistent paper/research pointer was changed.",
        }

    def create_proposal(self, db: Session, strategy_key: str, current_params: dict, proposed_params: dict, rationale: str, metrics_before: dict) -> dict[str, Any]:
        if not strategy_key or not all(isinstance(value, dict) for value in (current_params, proposed_params, metrics_before)) or not str(rationale).strip():
            return {"ok": False, "error": "valid_strategy_params_rationale_and_metrics_required"}
        record = LearningRecord(
            category="strategy_proposal", subject=strategy_key, outcome="proposed",
            lesson=str(rationale).strip(),
            evidence={
                "current_params": deepcopy(current_params), "proposed_params": deepcopy(proposed_params),
                "metrics_before": deepcopy(metrics_before), "tested": False, "test_passed": False,
                "approved_for_live": False, "applied_to_paper": False,
            },
            verified=False,
        )
        db.add(record); db.commit(); db.refresh(record)
        return {"ok": True, "proposal_id": record.id, "status": record.outcome}

    def test_proposal(self, db: Session, proposal_id: int, metrics_after_paper: dict) -> dict[str, Any]:
        record = db.get(LearningRecord, proposal_id)
        if record is None or record.category != "strategy_proposal":
            return {"ok": False, "error": "proposal_not_found"}
        evidence = deepcopy(record.evidence or {})
        # Every retest invalidates an earlier pass/approval until fresh metrics
        # are validated; malformed input must not preserve stale success.
        evidence.update({"tested": False, "test_passed": False, "approved_for_live": False, "walk_forward_validated": False})
        evidence.pop("walk_forward", None)
        evidence.pop("metrics_after_paper", None)
        record.evidence = evidence
        record.outcome = "paper_test_invalid"
        db.add(record)
        db.commit()
        metrics_before = evidence.get("metrics_before")
        if (not isinstance(metrics_before, dict) or not isinstance(metrics_after_paper, dict)
                or "expectancy" not in metrics_after_paper):
            return {"ok": False, "error": "paper_metrics_with_expectancy_required"}
        if any(isinstance(metrics.get(key), bool) for metrics in (
            metrics_before, metrics_after_paper,
        ) for key in ("expectancy", "max_drawdown")):
            return {"ok": False, "error": "boolean_paper_metric_not_allowed"}
        try:
            before = float(metrics_before.get("expectancy", 0.0))
            after = float(metrics_after_paper["expectancy"])
            before_dd = float(metrics_before.get("max_drawdown", 0.0))
            after_dd = float(metrics_after_paper.get("max_drawdown", before_dd))
        except (KeyError, TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "invalid_paper_metrics"}
        import math
        if not all(math.isfinite(value) for value in (before, after, before_dd, after_dd)):
            return {"ok": False, "error": "non_finite_paper_metrics"}
        if before_dd < 0 or after_dd < 0:
            return {"ok": False, "error": "negative_drawdown_not_allowed"}
        passed = after > before and after_dd <= before_dd
        evidence.update({"tested": True, "test_passed": passed, "metrics_after_paper": deepcopy(metrics_after_paper)})
        record.evidence = evidence
        record.outcome = "paper_test_passed" if passed else "paper_test_failed"
        db.add(record); db.commit()
        return {"ok": True, "proposal_id": proposal_id, "test_passed": passed, "status": record.outcome}

    def validate_walk_forward(
        self, db: Session, proposal_id: int,
        baseline_folds: list[dict[str, Any]], candidate_folds: list[dict[str, Any]],
        *, min_folds: int = 3, min_samples_per_fold: int = 30,
    ) -> dict[str, Any]:
        """Persist chronological walk-forward validation and an untouched final holdout."""
        record = db.get(LearningRecord, proposal_id)
        if record is None or record.category != "strategy_proposal":
            return {"ok": False, "error": "proposal_not_found"}
        evidence = deepcopy(record.evidence or {})
        evidence.update({"tested": False, "test_passed": False, "approved_for_live": False,
                         "walk_forward_validated": False})
        evidence.pop("walk_forward", None)
        record.evidence = evidence
        record.outcome = "walk_forward_validation_pending"
        db.add(record)
        db.commit()
        if (not isinstance(baseline_folds, list) or not isinstance(candidate_folds, list)
                or isinstance(min_folds, bool) or not isinstance(min_folds, int) or min_folds < 3
                or isinstance(min_samples_per_fold, bool) or not isinstance(min_samples_per_fold, int)
                or min_samples_per_fold < 10 or len(baseline_folds) != len(candidate_folds)
                or len(baseline_folds) < min_folds):
            return {"ok": False, "error": "insufficient_or_mismatched_walk_forward_folds"}
        import math
        parsed = []
        for index, (baseline, candidate) in enumerate(zip(baseline_folds, candidate_folds)):
            pair = []
            for row in (baseline, candidate):
                if not isinstance(row, dict) or any(isinstance(row.get(key), bool) for key in (
                    "expectancy", "max_drawdown", "sample_count"
                )):
                    return {"ok": False, "error": f"invalid_walk_forward_fold_{index}"}
                try:
                    expectancy = float(row["expectancy"])
                    drawdown = float(row["max_drawdown"])
                    raw_samples = row["sample_count"]
                    if type(raw_samples) is not int:
                        raise ValueError("sample_count_must_be_integer")
                    samples = raw_samples
                except (KeyError, TypeError, ValueError, OverflowError):
                    return {"ok": False, "error": f"invalid_walk_forward_fold_{index}"}
                if (not math.isfinite(expectancy) or not math.isfinite(drawdown)
                        or drawdown < 0 or samples < min_samples_per_fold):
                    return {"ok": False, "error": f"walk_forward_fold_below_quality_gate_{index}"}
                pair.append({"expectancy": expectancy, "max_drawdown": drawdown, "sample_count": samples})
            parsed.append((pair[0], pair[1]))
        # The last chronological fold is a final holdout. It is evaluated only
        # after the walk-forward folds and must not influence their aggregate.
        validation_pairs = parsed[:-1]
        baseline_mean = sum(pair[0]["expectancy"] for pair in validation_pairs) / len(validation_pairs)
        candidate_mean = sum(pair[1]["expectancy"] for pair in validation_pairs) / len(validation_pairs)
        baseline_dd = sum(pair[0]["max_drawdown"] for pair in validation_pairs) / len(validation_pairs)
        candidate_dd = sum(pair[1]["max_drawdown"] for pair in validation_pairs) / len(validation_pairs)
        holdout_base, holdout_candidate = parsed[-1]
        all_dd_non_worse = all(candidate["max_drawdown"] <= baseline["max_drawdown"]
                               for baseline, candidate in parsed)
        passed = (candidate_mean > baseline_mean and candidate_dd <= baseline_dd
                  and all_dd_non_worse
                  and holdout_candidate["expectancy"] > holdout_base["expectancy"])
        evidence.update({
            "tested": True, "test_passed": passed, "walk_forward_validated": passed,
            "walk_forward": {
                "folds": len(validation_pairs), "total_folds": len(parsed),
                "baseline_expectancy": baseline_mean,
                "candidate_expectancy": candidate_mean, "baseline_avg_drawdown": baseline_dd,
                "candidate_avg_drawdown": candidate_dd,
                "holdout_expectancy_improved": holdout_candidate["expectancy"] > holdout_base["expectancy"],
                "all_fold_drawdowns_non_worsening": all_dd_non_worse,
                "minimum_samples_per_fold": min_samples_per_fold,
                "holdout_sample_count": holdout_candidate["sample_count"], "research_only": True,
            },
            "metrics_after_paper": {
                "expectancy": candidate_mean, "max_drawdown": candidate_dd,
                "sample_count": sum(pair[1]["sample_count"] for pair in validation_pairs),
            },
        })
        record.evidence = evidence
        record.outcome = "walk_forward_passed" if passed else "walk_forward_failed"
        db.add(record)
        db.commit()
        return {"ok": True, "proposal_id": proposal_id, "test_passed": passed,
                "walk_forward_validated": passed, "status": record.outcome,
                "walk_forward": deepcopy(evidence["walk_forward"])}

    def approve_for_live(self, db: Session, proposal_id: int, *, explicit_approval: bool = False) -> dict[str, Any]:
        if explicit_approval is not True:
            return {"ok": False, "error": "explicit_approval_required"}
        record = db.get(LearningRecord, proposal_id)
        if record is None or record.category != "strategy_proposal":
            return {"ok": False, "error": "proposal_not_found"}
        evidence = deepcopy(record.evidence or {})
        if not evidence.get("tested") or not evidence.get("test_passed") or not evidence.get("walk_forward_validated"):
            return {"ok": False, "error": "successful_walk_forward_validation_required"}
        evidence["approved_for_live"] = True
        record.evidence = evidence
        record.outcome = "approved_pending_separate_live_integration"
        db.add(record); db.commit()
        return {"ok": True, "proposal_id": proposal_id, "applied": False, "live_applied": False}

    def apply_proposal_to_paper(self, db: Session, proposal_id: int, *, explicit_approval: bool = False) -> dict[str, Any]:
        if explicit_approval is not True:
            return {"ok": False, "error": "explicit_approval_required"}
        record = db.get(LearningRecord, proposal_id)
        if record is None or record.category != "strategy_proposal":
            return {"ok": False, "error": "proposal_not_found"}
        evidence = deepcopy(record.evidence or {})
        if evidence.get("applied_to_paper") is True:
            return {"ok": False, "error": "proposal_already_applied_to_paper"}
        if not evidence.get("tested") or not evidence.get("test_passed") or not evidence.get("walk_forward_validated"):
            return {"ok": False, "error": "successful_walk_forward_validation_required"}
        params = evidence.get("proposed_params")
        metrics = evidence.get("metrics_after_paper")
        if not isinstance(params, dict) or not isinstance(metrics, dict):
            return {"ok": False, "error": "valid_tested_params_and_metrics_required"}
        # Keep the version, active pointer, and proposal state in one DB
        # transaction. A failed commit must not leave a half-applied proposal.
        versions = self.list_versions(db, record.subject, limit=500)
        version_number = max((int(item.get("version") or 0) for item in versions), default=0) + 1
        version_record = LearningRecord(
            category="strategy_version", subject=record.subject,
            outcome=f"version_{version_number}",
            lesson="Persisted research/paper strategy parameter snapshot; not a live configuration change.",
            evidence={"version": version_number, "params": deepcopy(params), "metrics": deepcopy(metrics)},
            verified=False,
        )
        db.add(version_record)
        db.flush()
        self._set_active_record(db, record.subject, version_number, version_record.id, reason="proposal_applied_to_paper")
        evidence["applied_to_paper"] = True
        evidence["applied_version"] = version_number
        record.evidence = evidence
        record.outcome = "applied_to_paper_version_store"
        db.add(record)
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        return {"ok": True, "proposal_id": proposal_id, "version": version_number, "live_applied": False}
