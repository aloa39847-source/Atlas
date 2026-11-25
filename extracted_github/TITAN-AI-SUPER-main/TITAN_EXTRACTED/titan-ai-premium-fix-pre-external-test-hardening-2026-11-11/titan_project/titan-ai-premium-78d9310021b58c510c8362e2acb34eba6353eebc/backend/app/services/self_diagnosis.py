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
            analysis_providers = []
            for item in providers:
                if not item.get("enabled"):
                    continue
                tasks = item.get("tasks") or []
                if tasks and not {str(task).strip().lower() for task in tasks}.intersection(
                    {"analysis", "decision", "trading_analysis"}
                ):
                    continue
                analysis_providers.append(item)

            local = local_model_status()
            local_ready = bool(
                local.get("enabled") is True
                and local.get("connected") is True
                and local.get("model_available") is True
            )
            primary = analysis_providers[0] if analysis_providers else None
            primary_name = (primary.get("provider") or "").lower() if primary else None
            untested = [
                item for item in analysis_providers
                if not item.get("ready") or item.get("last_test_success") is not True
            ]
            tested_count = len(analysis_providers) - len(untested)

            if analysis_providers and not untested:
                return {
                    "status": "ok",
                    "detail": (
                        f"All {tested_count} enabled cloud analysis advisors are key-ready and connection-tested; "
                        f"primary by configured priority: {primary_name}; "
                        f"local fallback {'ready' if local_ready else 'not ready'}."
                    ),
                    "primary_provider": primary_name,
                    "cloud_advisor_count": len(analysis_providers),
                    "connection_tested_count": tested_count,
                    "local_fallback_connected": local_ready,
                }
            if analysis_providers:
                first_ready = bool(primary and primary.get("ready"))
                first_tested = bool(primary and primary.get("last_test_success") is True)
                return {
                    "status": "warning",
                    "detail": (
                        f"{tested_count}/{len(analysis_providers)} enabled analysis advisors passed connection tests; "
                        f"priority primary {primary_name} "
                        f"({'tested' if first_ready and first_tested else 'not successfully tested'})."
                    ),
                    "primary_provider": primary_name,
                    "cloud_advisor_count": len(analysis_providers),
                    "connection_tested_count": tested_count,
                    "untested_providers": [
                        (item.get("provider") or "unknown") for item in untested
                    ],
                    "local_fallback_connected": local_ready,
                }
            if local_ready:
                return {
                    "status": "warning",
                    "detail": "Local advisory runtime is ready, but no enabled cloud analysis advisors are configured.",
                    "primary_provider": "local",
                    "cloud_advisor_count": 0,
                    "connection_tested_count": 0,
                    "local_fallback_connected": True,
                }
            return {
                "status": "warning",
                "detail": "No enabled, connection-tested cloud analysis advisors or ready local model are available.",
                "primary_provider": None,
                "cloud_advisor_count": 0,
                "connection_tested_count": 0,
                "local_fallback_connected": False,
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
            maturity_counts: dict[str, int] = {}
            runtime_counts: dict[str, int] = {}
            broken_runtime: list[str] = []
            unmapped_integration: list[str] = []
            for engine in engs:
                maturity = str(engine.get("status", "unknown")).lower()
                maturity_counts[maturity] = maturity_counts.get(maturity, 0) + 1
                runtime = engine.get("runtime_check") or {}
                runtime_status = str(runtime.get("status", "not_checked")).lower()
                runtime_counts[runtime_status] = runtime_counts.get(runtime_status, 0) + 1
                if runtime_status != "wired":
                    broken_runtime.append(f"{engine.get('key', 'unknown')}:{runtime_status}")
                integration = engine.get("integration") or {}
                if integration.get("mode") in {None, "unmapped"}:
                    unmapped_integration.append(str(engine.get("key", "unknown")))

            if not engs:
                return {"status": "error", "detail": "No registered engines were found."}
            if broken_runtime or unmapped_integration:
                return {
                    "status": "error",
                    "detail": (
                        f"{len(broken_runtime)} engine runtime contracts are broken or missing; "
                        f"{len(unmapped_integration)} engines have no integration mapping."
                    ),
                    "maturity_counts": maturity_counts,
                    "runtime_counts": runtime_counts,
                    "broken_runtime": broken_runtime[:20],
                    "unmapped_integration": unmapped_integration[:20],
                }

            partial_count = sum(
                count for status, count in maturity_counts.items() if status != "ready"
            )
            detail = (
                f"{len(engs)} registered engines; all {runtime_counts.get('wired', 0)} "
                "runtime import/callable contracts resolve and all integration modes are mapped. "
                f"Implementation maturity remains partial/not-ready for {partial_count} engines. "
                "This check does not prove external connectivity or end-to-end behavior."
            )
            return {
                "status": "warning" if partial_count else "ok",
                "detail": detail,
                "maturity_counts": maturity_counts,
                "runtime_counts": runtime_counts,
                "runtime_wired_count": runtime_counts.get("wired", 0),
                "integration_mapped_count": len(engs) - len(unmapped_integration),
                "verification_scope": "import_and_callable_only",
            }
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
