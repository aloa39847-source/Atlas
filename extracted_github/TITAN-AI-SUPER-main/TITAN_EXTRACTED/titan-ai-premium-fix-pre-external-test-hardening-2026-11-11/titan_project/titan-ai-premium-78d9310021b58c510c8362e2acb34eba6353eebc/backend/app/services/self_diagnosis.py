"""Autonomous Self-Diagnosis & Self-Healing Engine for TITAN AI PREMIUM.

Monitors all system components, detects errors, diagnoses issues, and
attempts safe auto-recovery. Reports to chat and self-healing dashboard.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.models import db as dbmod
from app.core.config import settings


class SelfDiagnosisEngine:
    def __init__(self):
        self._last_check: datetime | None = None
        self._issues: list[dict[str, Any]] = []
        self._healed: list[dict[str, Any]] = []
        self._pending_approval: list[dict[str, Any]] = []

    def run_full_check(self) -> dict[str, Any]:
        """Run a comprehensive health check across all components."""
        # Each report describes only the current run; do not retain stale issues.
        self._issues = []
        self._healed = []
        self._pending_approval = []
        db = dbmod.SessionLocal()
        results: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "overall": "healthy",
            "components": {},
            "issues": [],
            "healed": [],
            "pending_approval": [],
        }
        checks = [
            ("backend", self._check_backend),
            ("database", lambda: self._check_database(db)),
            ("knowledge", lambda: self._check_knowledge(db)),
            ("learning", lambda: self._check_learning(db)),
            ("ai_providers", lambda: self._check_ai_providers(db)),
            ("strategies", lambda: self._check_strategies(db)),
            ("decisions", lambda: self._check_decisions(db)),
            ("engines", lambda: self._check_engines(db)),
            ("binance_testnet", self._check_binance_testnet),
            ("risk_engine", self._check_risk),
        ]
        for name, fn in checks:
            try:
                r = fn()
                results["components"][name] = r
                if r.get("status") != "ok":
                    issue = {"component": name, "status": r.get("status"), "detail": r.get("detail", "")}
                    results["issues"].append(issue)
                    self._issues.append(issue)
            except Exception as e:
                results["components"][name] = {"status": "error", "detail": str(e)}
                results["issues"].append({"component": name, "status": "error", "detail": str(e)})
        if results["issues"]:
            results["overall"] = "degraded" if len(results["issues"]) < 3 else "critical"
        self._last_check = datetime.now(timezone.utc)
        db.close()
        return results

    def _check_backend(self) -> dict[str, Any]:
        return {"status": "ok", "detail": "Backend process running"}

    def _check_database(self, db) -> dict[str, Any]:
        try:
            db.execute(select(1))
            return {"status": "ok", "detail": "DB connection OK"}
        except Exception as e:
            return {"status": "error", "detail": str(e)}

    def _check_knowledge(self, db) -> dict[str, Any]:
        try:
            from app.models.entities import KnowledgeSource
            count = db.execute(select(KnowledgeSource)).scalars().all()
            return {"status": "ok", "detail": f"{len(count)} knowledge sources"}
        except Exception as e:
            return {"status": "error", "detail": str(e)}

    def _check_learning(self, db) -> dict[str, Any]:
        try:
            from app.models.entities import LearningRecord
            count = db.execute(select(LearningRecord)).scalars().all()
            return {"status": "ok", "detail": f"{len(count)} learning records"}
        except Exception as e:
            return {"status": "error", "detail": str(e)}

    def _check_ai_providers(self, db) -> dict[str, Any]:
        try:
            from app.integrations.ai_providers import provider_status
            from app.integrations.local_ai import local_model_status

            snapshot = provider_status(db)
            providers = snapshot.get("providers") or []
            openai_rows = [
                item for item in providers
                if (item.get("provider") or "").lower() == "openai" and item.get("enabled")
            ]
            # Match evaluator selection exactly: the lowest-priority enabled
            # OpenAI row is the primary; a tested secondary must not mask it.
            primary = openai_rows[0] if openai_rows else None
            openai_ready = bool(primary and primary.get("ready"))
            openai_tested = bool(primary and primary.get("last_test_success") is True)
            local = local_model_status()

            if openai_ready and openai_tested:
                fallback = "local fallback connected" if local.get("connected") else "local fallback unavailable"
                return {
                    "status": "ok",
                    "detail": f"OpenAI primary key configured and connection-tested; {fallback}.",
                    "primary_provider": "openai",
                    "local_fallback_connected": bool(local.get("connected")),
                }
            if openai_ready:
                return {
                    "status": "warning",
                    "detail": "OpenAI primary key is configured but a successful provider connection test has not been recorded.",
                    "primary_provider": "openai",
                    "local_fallback_connected": bool(local.get("connected")),
                }
            if local.get("connected") and local.get("model_available"):
                return {
                    "status": "warning",
                    "detail": "OpenAI primary is not ready; the local loopback fallback is available as fallback-only.",
                    "primary_provider": None,
                    "local_fallback_connected": True,
                }
            return {
                "status": "warning",
                "detail": "OpenAI primary is not configured and the local fallback is not confirmed ready.",
                "primary_provider": None,
                "local_fallback_connected": bool(local.get("connected")),
                "configured_provider_count": len(providers),
            }
        except Exception as e:
            return {"status": "error", "detail": str(e)}

    def _check_strategies(self, db) -> dict[str, Any]:
        try:
            from app.services.core_service import core_service
            strs = core_service.strategies()
            return {"status": "ok", "detail": f"{len(strs)} strategies"}
        except Exception as e:
            return {"status": "error", "detail": str(e)}

    def _check_decisions(self, db) -> dict[str, Any]:
        try:
            from app.models.entities import DecisionRecord
            count = db.execute(select(DecisionRecord)).scalars().all()
            return {"status": "ok", "detail": f"{len(count)} decisions recorded"}
        except Exception as e:
            return {"status": "error", "detail": str(e)}

    def _check_engines(self, db) -> dict[str, Any]:
        try:
            from app.services.core_service import core_service
            db2 = dbmod.SessionLocal()
            try:
                engs = core_service.engines(db2)
            finally:
                db2.close()
            counts: dict[str, int] = {}
            incomplete: list[str] = []
            for engine in engs:
                status = str(engine.get("status", "unknown")).lower()
                counts[status] = counts.get(status, 0) + 1
                if status != "ready":
                    incomplete.append(f"{engine.get('key', 'unknown')}:{status}")
            if incomplete:
                detail = (
                    f"{len(engs)} registered; {counts.get('ready', 0)} ready; "
                    f"{len(incomplete)} not verified ready: " + ", ".join(incomplete[:12])
                )
                return {"status": "warning", "detail": detail, "status_counts": counts}
            return {"status": "ok", "detail": f"{len(engs)} engines report ready", "status_counts": counts}
        except Exception as e:
            return {"status": "error", "detail": str(e)}

    def _check_binance_testnet(self) -> dict[str, Any]:
        try:
            from app.integrations.binance_adapter import BinanceTestnetAdapter, BinanceConfig
            from app.services.credentials import load_testnet_credentials
            creds = load_testnet_credentials()
            if not creds.get("configured"):
                return {"status": "warning", "detail": "Testnet not configured (no API keys)"}
            cfg = BinanceConfig(api_key=creds["api_key"], api_secret=creds["api_secret"], market=creds.get("market", "spot"))
            adapter = BinanceTestnetAdapter(cfg)
            st = adapter.status()
            if st.get("connected"):
                return {"status": "ok", "detail": f"Testnet connected ({st.get('market','spot')})"}
            return {"status": "warning", "detail": f"Testnet not connected: {st.get('note','')}"}
        except Exception as e:
            return {"status": "error", "detail": str(e)}

    def _check_risk(self) -> dict[str, Any]:
        try:
            from app.engines.risk import RiskEngine
            from app.core.enums import EnvironmentMode, MarketType
            result = RiskEngine().validate_trade(
                environment=EnvironmentMode.DEMO,
                market=MarketType.SPOT,
                balance=1000.0,
                risk_per_trade=0.005,
                entry=100.0,
                stop_loss=99.0,
                leverage=1.0,
            )
            if not result.get("ok"):
                return {"status": "error", "detail": f"Risk validation failed: {result.get('issues', [])}"}
            notional = float(result.get("position_notional_estimate", 0.0))
            quantity = float(result.get("quantity_estimate", 0.0))
            if notional <= 0 or notional > settings.max_spot_trade_notional or quantity <= 0 or result.get("leverage_used") != 1.0:
                return {"status": "error", "detail": "Risk validation returned unsafe Spot sizing or leverage."}
            return {"status": "ok", "detail": "Risk sizing self-test passed for a bounded Demo/Spot sample."}
        except Exception as e:
            return {"status": "error", "detail": str(e)}

    def get_issues(self) -> list[dict[str, Any]]:
        return self._issues

    def get_summary_text(self) -> str:
        if not self._issues:
            return "جميع المكونات تعمل بشكل سليم. لا توجد مشكلات."
        lines = [f"- {i['component']}: {i['status']} — {i['detail']}" for i in self._issues]
        return "المشكلات المكتشفة:\n" + "\n".join(lines)

    def attempt_auto_heal(self, issue: dict[str, Any]) -> dict[str, Any]:
        """Attempt safe auto-healing for known recoverable issues."""
        comp = issue.get("component", "")
        result = {"component": comp, "healed": False, "action": "none", "detail": ""}
        # Auto-heal: re-init DB if connection failed
        if comp == "database" and issue.get("status") == "error":
            try:
                dbmod.init_db()
                result["healed"] = True
                result["action"] = "reinit_db"
                result["detail"] = "Database re-initialized"
                self._healed.append(result)
            except Exception as e:
                result["detail"] = str(e)
        # Auto-heal: log warning for binance not configured (not auto-fixable, needs user input)
        if comp == "binance_testnet" and issue.get("status") == "warning":
            result["action"] = "user_input_required"
            result["detail"] = "Testnet API keys needed — user must configure"
            self._pending_approval.append(issue)
        return result


# Singleton
_engine: SelfDiagnosisEngine | None = None


def get_engine() -> SelfDiagnosisEngine:
    global _engine
    if _engine is None:
        _engine = SelfDiagnosisEngine()
    return _engine
