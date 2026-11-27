from __future__ import annotations

from datetime import datetime, timezone
import importlib
import math
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import EngineStatus, EnvironmentMode, MarketType, Timeframe
from app.engines.decision import DecisionEngine
from app.engines.liquidity import LiquidityAnalysisEngine
from app.engines.order_flow import OrderFlowEngine
from app.engines.market_data import MarketDataEngine
from app.engines.risk import RiskEngine
from app.engines.strategies import list_strategies
from app.engines.technical import TechnicalAnalysisEngine
from app.models.entities import DecisionRecord, EngineState, OrderRecord, PositionRecord, SystemState
from .audit import log_event
from .governance import record_config_change


ENGINE_REGISTRY = [
    ("compliance_policy", "TITAN COMPLIANCE & POLICY ENGINE", "implemented_partial"),
    ("market_analysis", "Market Analysis Engine", "implemented_partial"),
    ("technical_analysis", "Technical Analysis Engine", "implemented_partial"),
    ("fundamental_analysis", "Fundamental Analysis Engine", "implemented_partial"),
    ("news_analysis", "News Analysis Engine", "implemented_partial"),
    ("market_structure", "Market Structure Engine", "implemented_partial"),
    ("liquidity_analysis", "Liquidity Analysis Engine", "implemented_partial"),
    ("order_flow", "Order Flow Engine", "implemented_partial"),
    ("volume_analysis", "Volume Analysis Engine", "implemented_partial"),
    ("coin_discovery", "Coin Discovery Engine", "implemented_partial"),
    ("arbitrage_analysis", "Triangular Spot Arbitrage Analysis Engine", "implemented_partial"),
    ("strategy_selection", "Strategy Selection Engine", "implemented_partial"),
    ("decision_making", "Decision Making Engine", "implemented_partial"),
    ("risk_management", "Risk Management Engine", "implemented_partial"),
    ("portfolio_management", "Portfolio Management Engine", "implemented_partial"),
    ("trade_execution", "Trade Execution Engine", "implemented_partial"),
    ("position_management", "Position Management Engine", "implemented_partial"),
    ("backtesting", "Backtesting Engine", "implemented_partial"),
    ("ai_learning", "AI Learning Engine", "implemented_partial"),
    ("self_evolution", "Self-Evolution Engine", "implemented_partial"),
    ("knowledge_base", "Knowledge Base", "implemented_partial"),
    ("long_term_memory", "Long-Term Memory", "implemented_partial"),
    ("performance_evaluation", "Performance Evaluation Engine", "implemented_partial"),
    ("prediction_tracking", "Prediction Tracking Engine", "implemented_partial"),
    ("system_monitoring", "System Monitoring Engine", "implemented_partial"),

]

# Runtime integration is intentionally lifecycle-scoped. Not every registered
# subsystem belongs in every trading decision: backtests, discovery, fundamental
# snapshots, and learning/evolution have separate inputs and approval boundaries.
ENGINE_INTEGRATION_MAP = {
    "compliance_policy": {"mode": "order_guardrail", "integration": "Order gateway compliance checks", "decision_evidence": False, "authority": "may_reject_order"},
    "market_analysis": {"mode": "decision_pipeline", "integration": "MarketDataEngine -> TechnicalAnalysisEngine -> DecisionEngine", "decision_evidence": True, "authority": "evidence_only"},
    "technical_analysis": {"mode": "decision_pipeline", "integration": "TitanCoreService.analyze -> DecisionEngine", "decision_evidence": True, "authority": "evidence_only"},
    "fundamental_analysis": {"mode": "on_demand", "integration": "FundamentalAnalysisEngine coin/protocol snapshot methods", "decision_evidence": False, "authority": "no_trade_authority", "dependency": "CoinGecko coin ID or DefiLlama protocol slug"},
    "news_analysis": {"mode": "decision_pipeline", "integration": "TitanCoreService.analyze -> DecisionEngine (fresh official primary-source confidence modifier capped at 8%)", "decision_evidence": True, "authority": "bounded_context_only"},
    "market_structure": {"mode": "decision_pipeline", "integration": "TechnicalAnalysisEngine market-structure output", "decision_evidence": True, "authority": "evidence_only"},
    "liquidity_analysis": {"mode": "decision_pipeline", "integration": "TitanCoreService.analyze -> microstructure evidence", "decision_evidence": True, "authority": "evidence_only"},
    "order_flow": {"mode": "decision_pipeline", "integration": "TitanCoreService.analyze -> microstructure evidence", "decision_evidence": True, "authority": "evidence_only"},
    "volume_analysis": {"mode": "decision_pipeline", "integration": "TechnicalAnalysisEngine volume/VWAP/volume-spike outputs", "decision_evidence": True, "authority": "evidence_only"},
    "coin_discovery": {"mode": "on_demand", "integration": "Read-only market scanner/discovery endpoints", "decision_evidence": False, "authority": "candidate_discovery_only"},
    "arbitrage_analysis": {"mode": "on_demand", "integration": "ArbitrageAnalysisEngine public Spot top-of-book route analysis", "decision_evidence": False, "authority": "estimate_only_no_execution"},
    "strategy_selection": {"mode": "decision_pipeline", "integration": "DecisionEngine.evaluate_strategy_candidates", "decision_evidence": True, "authority": "candidate_selection_only"},
    "decision_making": {"mode": "decision_pipeline", "integration": "TitanCoreService.analyze -> DecisionEngine.decide", "decision_evidence": True, "authority": "final_deterministic_decision"},
    "risk_management": {"mode": "independent_guardrail", "integration": "DecisionEngine RiskEngine plus supervised worker risk checks", "decision_evidence": True, "authority": "independent_veto"},
    "portfolio_management": {"mode": "decision_pipeline", "integration": "Open-position exposure/count checks before decision sizing", "decision_evidence": True, "authority": "exposure_guardrail"},
    "trade_execution": {"mode": "supervised_execution", "integration": "Binance execution service via supervised trading worker", "decision_evidence": False, "authority": "supervised_worker_only_explicit_live_start_confirmation"},
    "position_management": {"mode": "position_lifecycle", "integration": "Persistent position ledger and supervised exchange reconciliation", "decision_evidence": False, "authority": "no_independent_order_authority"},
    "backtesting": {"mode": "on_demand", "integration": "BacktestEngine and portfolio backtest endpoints", "decision_evidence": False, "authority": "simulation_only"},
    "ai_learning": {"mode": "learning_context", "integration": "Historical outcome memory supplied to DecisionEngine and learning APIs", "decision_evidence": True, "authority": "advisory_no_auto_strategy_change"},
    "self_evolution": {"mode": "approval_gated_lifecycle", "integration": "PersistentStrategyLearning proposals, historical walk-forward validation, and explicit paper-apply workflow", "decision_evidence": False, "authority": "explicit_approval_required"},
    "knowledge_base": {"mode": "on_demand", "integration": "Knowledge concept retrieval and verification APIs", "decision_evidence": False, "authority": "reference_only"},
    "long_term_memory": {"mode": "decision_pipeline", "integration": "build_strategy_memory_context supplied to DecisionEngine and specialist agents", "decision_evidence": True, "authority": "historical_context_only"},
    "performance_evaluation": {"mode": "post_trade_analytics", "integration": "PerformanceAnalyticsService over persisted trade outcomes", "decision_evidence": False, "authority": "reporting_only"},
    "prediction_tracking": {"mode": "post_prediction_lifecycle", "integration": "PredictionTrackerService evaluation of matured predictions", "decision_evidence": False, "authority": "evaluation_only"},
    "system_monitoring": {"mode": "monitoring", "integration": "System health, engine registry and operational diagnostics endpoints", "decision_evidence": False, "authority": "monitoring_only"},
}

# Import/callable contracts for every registered engine. These checks verify
# that the declared adapter exists and is callable; they do not claim that an
# external exchange, provider, or data feed is currently connected.
ENGINE_RUNTIME_CONTRACTS = {
    "compliance_policy": ["app.engines.compliance:compliance_engine.check_order_action"],
    "market_analysis": [
        "app.engines.market_data:MarketDataEngine.get_public_multi_timeframe",
        "app.engines.technical:TechnicalAnalysisEngine.analyze",
        "app.engines.decision:DecisionEngine.decide",
    ],
    "technical_analysis": ["app.engines.technical:TechnicalAnalysisEngine.analyze"],
    "fundamental_analysis": [
        "app.engines.fundamental:FundamentalAnalysisEngine.fetch_and_analyze",
        "app.engines.fundamental:FundamentalAnalysisEngine.fetch_protocol_and_analyze",
    ],
    "news_analysis": ["app.engines.news:NewsAnalysisEngine.fetch_and_analyze"],
    "market_structure": ["app.engines.technical:TechnicalAnalysisEngine.analyze"],
    "liquidity_analysis": ["app.engines.liquidity:LiquidityAnalysisEngine.fetch_and_analyze"],
    "order_flow": ["app.engines.order_flow:OrderFlowEngine.fetch_and_analyze"],
    "volume_analysis": ["app.engines.technical:TechnicalAnalysisEngine.analyze"],
    "coin_discovery": ["app.services.smart_scanner:SmartScannerService.scan"],
    "arbitrage_analysis": ["app.engines.arbitrage:ArbitrageAnalysisEngine.fetch_and_analyze"],
    "strategy_selection": ["app.engines.strategies:evaluate_strategy_candidates"],
    "decision_making": ["app.engines.decision:DecisionEngine.decide"],
    "risk_management": ["app.engines.risk:RiskEngine.validate_trade"],
    "portfolio_management": [
        "app.services.shadow_trading:ShadowTradingService.get_portfolio",
        "app.services.shadow_trading:ShadowTradingService.create_shadow_order",
    ],
    "trade_execution": [
        "app.services.binance_execution:BinanceExecutionService.preflight",
        "app.services.binance_execution:BinanceExecutionService.execute_order",
    ],
    "position_management": [
        "app.services.binance_trading_worker:_manage_positions",
        "app.services.position_manager:PositionRiskManager.evaluate_positions",
    ],
    "backtesting": ["app.engines.backtest:BacktestEngine.run"],
    "ai_learning": ["app.learning.outcomes:build_strategy_memory_context"],
    "self_evolution": [
        "app.learning.persistent:PersistentStrategyLearning.create_proposal",
        "app.learning.persistent:PersistentStrategyLearning.validate_walk_forward",
    ],
    "knowledge_base": [
        "app.knowledge.base:list_concepts",
        "app.knowledge.base:categories",
    ],
    "long_term_memory": ["app.learning.outcomes:build_strategy_memory_context"],
    "performance_evaluation": ["app.services.performance_analytics:PerformanceAnalyticsService.evaluate"],
    "prediction_tracking": ["app.services.prediction_tracker:PredictionTrackerService.evaluate_matured"],
    "system_monitoring": ["app.services.self_diagnosis:SelfDiagnosisEngine.run_full_check"],
}


def inspect_engine_runtime_contract(engine_key: str) -> dict[str, Any]:
    """Resolve an engine's declared import/callable contract without side effects."""
    contracts = ENGINE_RUNTIME_CONTRACTS.get(engine_key)
    if not contracts:
        return {
            "status": "unmapped",
            "checks": [],
            "scope": "import_and_callable_only",
            "note": "No runtime contract is declared for this registered engine.",
        }
    checks = []
    for contract in contracts:
        module_name, symbol_path = contract.split(":", 1)
        try:
            target: Any = importlib.import_module(module_name)
            for part in symbol_path.split("."):
                target = getattr(target, part)
            ok = callable(target)
            checks.append({"contract": contract, "ok": ok,
                           **({} if ok else {"error": "target_not_callable"})})
        except Exception as exc:
            checks.append({"contract": contract, "ok": False,
                           "error": type(exc).__name__})
    passed = sum(1 for item in checks if item["ok"])
    return {
        "status": "wired" if passed == len(checks) else "broken",
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "scope": "import_and_callable_only",
        "note": "Callable contract only; live credentials, external connectivity, and end-to-end behavior require separate tests.",
    }



def _utcnow():
    return datetime.now(timezone.utc)


class TitanCoreService:
    def __init__(self) -> None:
        self.market_data = MarketDataEngine()
        self.technical = TechnicalAnalysisEngine()
        self.risk = RiskEngine()
        self.decision = DecisionEngine()

    def ensure_bootstrap(self, db: Session) -> None:
        state = db.get(SystemState, 1)
        if not state:
            state = SystemState(
                id=1, environment_mode=EnvironmentMode.DEMO.value, account_target="demo", market_type=MarketType.SPOT.value,
                bot_run_state="stopped", autonomous_enabled=False, live_trading_enabled=False, live_confirmed_by_user=False,
                emergency_stop=False, selected_timeframes=[t.value for t in Timeframe],
                risk_limits={"demo_initial_balance": 150.0, "demo_risk_per_trade": 0.005,
                             "max_leverage_cap": 1.0, "max_risk_per_trade_cap": min(settings.max_risk_per_trade_cap, 0.005),
                             "max_daily_loss_cap": settings.max_daily_loss_cap, "max_open_positions_cap": settings.max_open_positions_cap},
                status_summary={"bootstrapped": True},
            )
            db.add(state)
            log_event(db, action="system.bootstrap", message="Initialized TITAN system state (DEMO, SPOT, stopped, live off).")
        else:
            # Compatibility defaults for older DB rows.
            if not getattr(state, "account_target", None):
                state.account_target = state.environment_mode or "demo"
            if not getattr(state, "bot_run_state", None):
                state.bot_run_state = "stopped"

            # Execution capability is controlled by server configuration and
            # the explicit Live Start action. Bootstrap must never revoke an
            # already-running supervised worker or clear its user confirmation.
            state.live_trading_enabled = bool(settings.live_trading_enabled)
            if (str(getattr(state, "account_target", "") or "").lower() != "live"
                    and str(getattr(state, "environment_mode", "") or "").lower() != "live"):
                state.live_confirmed_by_user = False
            db.add(state)
        existing_rows = {
            row.engine_key: row for row in db.execute(select(EngineState)).scalars().all()
        }
        for key, name, status in ENGINE_REGISTRY:
            row = existing_rows.get(key)
            if row is None:
                db.add(EngineState(engine_key=key, name=name, status=status, message="Registered by TITAN AI CORE bootstrap."))
            else:
                # Registry status describes implementation maturity, not a
                # runtime start/stop flag. Keep it synchronized after upgrades.
                row.name = name
                row.status = status
                if not row.message:
                    row.message = "Implementation status synchronized from TITAN AI CORE registry."
                db.add(row)
        db.commit()

    def _state(self, db: Session) -> SystemState:
        self.ensure_bootstrap(db)
        return db.get(SystemState, 1)

    def get_state(self, db: Session) -> dict[str, Any]:
        s = self._state(db)
        return {
            "environment_mode": s.environment_mode,
            "account_target": getattr(s, "account_target", s.environment_mode),
            "market_type": s.market_type,
            "bot_run_state": getattr(s, "bot_run_state", "stopped"),
            "bot_started_at": s.bot_started_at.isoformat() if getattr(s, "bot_started_at", None) else None,
            "bot_stopped_at": s.bot_stopped_at.isoformat() if getattr(s, "bot_stopped_at", None) else None,
            "bot_stop_reason": getattr(s, "bot_stop_reason", None),
            "autonomous_enabled": s.autonomous_enabled,
            "live_trading_enabled": s.live_trading_enabled,
            "live_confirmed_by_user": bool(getattr(s, "live_confirmed_by_user", False)),
            "emergency_stop": s.emergency_stop,
            "trading_paused": bool((s.risk_limits or {}).get("trading_paused", False)),
            "selected_timeframes": s.selected_timeframes or [t.value for t in Timeframe],
            "risk_limits": s.risk_limits or {},
            "safety": {
                "allow_demo_to_live_auto": settings.allow_demo_to_live_auto,
                "allow_withdrawal_permissions": settings.allow_withdrawal_permissions,
                "note": "A Binance Testnet account may be authenticated for read-only checks, but its worker remains stopped until API-key TRADE permission is independently validated and the full preflight passes. Live execution is disabled by default and remains read-only until a separate explicit approval and all server, eligibility, API-permission and risk checks pass. Withdrawal/transfer permissions are never supported.",
            },
            "master_control": {
                "start_requires": ["account_target_selected", "connectivity_or_data_check", "compliance_check", "risk_limits", "no_duplicate_run", "explicit_live_user_confirmation_for_real_account"],
                "stop_default": "stop_new_only",
                "emergency_stop": "independent_flag_blocks_new_actions",
            },
        }

    def set_state(self, db: Session, *, environment_mode: str | None = None, account_target: str | None = None,
                  market_type: str | None = None, autonomous_enabled: bool | None = None, emergency_stop: bool | None = None,
                  live_confirm: bool | None = None) -> dict[str, Any]:
        s = self._state(db)
        old = self.get_state(db)
        requested_targets: list[str] = []
        if account_target is not None:
            requested_account_target = str(account_target).strip().lower()
            if requested_account_target not in {"demo", "testnet", "live"}:
                raise ValueError("account_target must be demo, testnet, or live")
            requested_targets.append(requested_account_target)
        if environment_mode is not None:
            requested_environment = EnvironmentMode(environment_mode)
            requested_targets.append("live" if requested_environment == EnvironmentMode.LIVE else "demo")
        if len(set(requested_targets)) > 1:
            raise ValueError("account_target_environment_mode_mismatch")

        active_states = {"starting", "running", "analyzing", "executing", "waiting_data"}
        if str(getattr(s, "bot_run_state", "stopped") or "stopped").lower() in active_states:
            current_target = str(getattr(s, "account_target", "demo") or "demo").strip().lower()
            current_market = str(getattr(s, "market_type", "spot") or "spot").strip().lower()
            requested_market = str(market_type).strip().lower() if market_type is not None else current_market
            if (any(target != current_target for target in requested_targets)
                    or requested_market != current_market):
                raise ValueError("cannot_change_account_or_market_while_worker_active")
        requested_live = (
            str(account_target or "").strip().lower() == "live"
            or str(environment_mode or "").strip().lower() == EnvironmentMode.LIVE.value
        )
        if requested_live and live_confirm is True and not bool(settings.live_trading_enabled):
            raise ValueError("live_trading_disabled_by_server")
        if account_target:
            at = str(account_target).strip().lower()
            if at not in {"demo", "testnet", "live"}:
                raise ValueError("account_target must be demo, testnet, or live")
            s.account_target = at
            s.environment_mode = "live" if at == "live" else "demo"
            # Selecting Live only enables read-only account monitoring. A
            # separate explicit Start action is required to confirm execution.
            s.live_confirmed_by_user = bool(live_confirm is True and at == "live")
            s.live_trading_enabled = bool(settings.live_trading_enabled)
        if environment_mode:
            env = EnvironmentMode(environment_mode)
            s.environment_mode = env.value
            s.account_target = "live" if env == EnvironmentMode.LIVE else "demo"
            s.live_confirmed_by_user = bool(live_confirm is True and env == EnvironmentMode.LIVE)
            s.live_trading_enabled = bool(settings.live_trading_enabled)
        if market_type:
            s.market_type = MarketType(market_type).value
        if autonomous_enabled is not None:
            if type(autonomous_enabled) is not bool:
                raise ValueError("autonomous_enabled_must_be_boolean")
            s.autonomous_enabled = autonomous_enabled
        if emergency_stop is not None:
            if type(emergency_stop) is not bool:
                raise ValueError("emergency_stop_must_be_boolean")
            if s.emergency_stop and emergency_stop is False:
                raise ValueError("emergency_stop_reset_requires_verified_reset_flow")
            if emergency_stop:
                s.emergency_stop = True
                s.bot_run_state = "stopped"
                s.autonomous_enabled = False
                s.bot_stopped_at = _utcnow()
                s.bot_stop_reason = "emergency_stop"
                s.live_confirmed_by_user = False
                log_event(db, action="master.emergency_stop", severity="warning", message="Emergency stop activated: new actions must halt.")
        db.add(s)
        record_config_change(db, actor="api", scope="system", key="state", old_value=old, new_value=self.get_state(db), reason="User/API state update", approved=True)
        log_event(db, action="state.update", message="System state updated.", details={"before_env": old.get("environment_mode"), "after_env": s.environment_mode})
        db.commit()
        return self.get_state(db)

    def set_trading_paused(self, db: Session, paused: bool) -> dict[str, Any]:
        if type(paused) is not bool:
            raise ValueError("trading_paused_must_be_boolean")
        state = self._state(db)
        if state.emergency_stop and not paused:
            raise ValueError("emergency_stop_must_be_reset_before_resuming_trading")
        old = self.get_state(db)
        limits = dict(state.risk_limits or {})
        limits["trading_paused"] = paused
        state.risk_limits = limits
        db.add(state)
        log_event(
            db, action="master.trading_paused" if paused else "master.trading_resumed",
            severity="warning" if paused else "info",
            message=("New trade entries paused; Binance connection and position management remain active."
                     if paused else "New trade entries resumed."),
            environment_mode=state.environment_mode, market_type=state.market_type,
        )
        record_config_change(db, actor="api", scope="system", key="trading_paused",
                             old_value=old.get("trading_paused", False), new_value=paused,
                             reason="User trading-only pause control", approved=True)
        db.commit()
        return {"ok": True, "state": self.get_state(db),
                "note": "Only new trade entries are paused; existing positions remain managed and exchange connectivity stays active."}

    def reset_emergency_stop(self, db: Session) -> dict[str, Any]:
        state = self._state(db)
        target = str(getattr(state, "account_target", "demo") or "demo").lower()
        market = str(state.market_type or "spot").lower()
        if target not in {"demo", "testnet", "live"} or market not in {
            MarketType.SPOT.value, MarketType.FUTURES.value,
        }:
            raise ValueError("emergency_reset_account_or_market_unsupported")
        shadow = target == "demo"
        uncertain = db.execute(select(OrderRecord).where(
            OrderRecord.environment_mode == target, OrderRecord.market_type == market,
            OrderRecord.shadow.is_(shadow),
            OrderRecord.status.in_(["pending", "new", "submitted", "partially_filled", "unknown"]),
        )).scalars().all()
        if uncertain:
            raise ValueError("reconciliation_required_before_emergency_reset")

        if target == "demo":
            if market == MarketType.FUTURES.value:
                open_positions = db.execute(select(PositionRecord).where(
                    PositionRecord.environment_mode == target,
                    PositionRecord.market_type == market,
                    PositionRecord.shadow.is_(True),
                    PositionRecord.status == "open",
                )).scalars().all()
                if open_positions:
                    raise ValueError("open_futures_positions_require_review_before_emergency_reset")
        else:
            from app.services.binance_execution import binance_execution_service
            adapter = binance_execution_service.adapter_for(state, live_worker_ready=False)
            status = adapter.status()
            if not status.get("connected"):
                raise ValueError("binance_account_connection_required_before_emergency_reset")
            local_open = db.execute(select(OrderRecord).where(
                OrderRecord.environment_mode == target, OrderRecord.market_type == market,
                OrderRecord.shadow.is_(False),
                OrderRecord.status.in_(["pending", "new", "submitted", "partially_filled", "unknown"]),
            )).scalars().all()
            reconciliation = adapter.reconcile(local_open_orders=[{
                "id": row.exchange_order_id or row.id,
                "orderId": row.exchange_order_id,
                "clientOrderId": row.client_order_id,
            } for row in local_open])
            if not reconciliation.get("ok"):
                raise ValueError("binance_order_reconciliation_required_before_emergency_reset")
            if market == MarketType.FUTURES.value:
                from app.services.binance_trading_worker import _verify_futures_position_sync
                _verify_futures_position_sync(db, adapter, target)

        batches = self.market_data.get_public_multi_timeframe(
            "BTC/USDT", market_type=market,
        )
        for timeframe, batch in batches.items():
            if (not batch.df or len(batch.df) < 30
                    or getattr(batch, "market_type", market) != market
                    or batch.source.value != "live_public"):
                raise ValueError(f"market_data_not_ready:{timeframe.value}")
        five_minute = batches[Timeframe.M5].df.rows[-1]
        stamp = five_minute.get("close_time") or five_minute.get("timestamp")
        if isinstance(stamp, bool) or stamp is None:
            raise ValueError("market_data_timestamp_invalid_during_emergency_reset")
        if isinstance(stamp, (int, float)):
            age = datetime.now(timezone.utc).timestamp() - float(stamp) / 1000.0
        elif isinstance(stamp, str):
            age = datetime.now(timezone.utc).timestamp() - datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
        else:
            raise ValueError("market_data_timestamp_invalid_during_emergency_reset")
        if not math.isfinite(age) or age > 300 or age < -60:
            raise ValueError("market_data_stale_during_emergency_reset")
        if market == MarketType.FUTURES.value:
            mark = self.market_data.get_public_mark_price("BTC/USDT")
            raw_price, raw_time = mark.get("mark_price"), mark.get("time")
            if (mark.get("source") != "binance_usdm_futures_public_read_only"
                    or isinstance(raw_price, bool) or isinstance(raw_time, bool)):
                raise ValueError("futures_mark_price_unavailable_during_emergency_reset")
            try:
                mark_price, mark_age = float(raw_price), datetime.now(timezone.utc).timestamp() - float(raw_time) / 1000.0
            except (TypeError, ValueError, OverflowError):
                raise ValueError("futures_mark_price_invalid_during_emergency_reset")
            if not math.isfinite(mark_price) or mark_price <= 0 or not math.isfinite(mark_age) or mark_age > 60 or mark_age < -60:
                raise ValueError("futures_mark_price_stale_during_emergency_reset")
        state.emergency_stop = False
        state.bot_run_state = "stopped"
        state.autonomous_enabled = False
        state.bot_stop_reason = "emergency_reset_checks_passed_worker_not_started"
        state.bot_stopped_at = _utcnow()
        db.add(state)
        log_event(db, action="master.emergency_reset", severity="warning",
                  message=f"Emergency stop reset after {target.title()}/{market.title()} order, account and fresh public-data checks; worker remains stopped.",
                  environment_mode=state.environment_mode, market_type=market)
        db.commit()
        return {"ok": True, "state": self.get_state(db),
                "note": "Safety checks passed. Emergency stop cleared, but no worker was started."}

    def start_bot(self, db: Session, *, account_target: str | None = None, live_confirm: bool = False) -> dict[str, Any]:
        # Starting Live is the explicit user authorization boundary. Merely
        # saving Live credentials never starts the worker or places an order.
        if account_target is not None:
            target = str(account_target).strip().lower()
            if target not in {"demo", "testnet", "live"}:
                raise ValueError("account_target_must_be_demo_testnet_or_live")
            if target == "live" and not bool(settings.live_trading_enabled):
                raise ValueError("live_trading_disabled_by_server")
            self.set_state(
                db, account_target=target,
                live_confirm=(live_confirm if target == "live" else None),
            )
        s = self._state(db)
        if account_target is None and str(getattr(s, "account_target", "demo") or "demo").lower() == "live" and live_confirm is True:
            self.set_state(db, account_target="live", live_confirm=True)
            s = self._state(db)
        target = str(getattr(s, "account_target", "demo") or "demo").lower()
        if target == "live" and (
            not bool(settings.live_trading_enabled)
            or not bool(getattr(s, "live_trading_enabled", False))
        ):
            raise ValueError("live_trading_disabled_by_server")
        market = str(s.market_type or "spot").lower()
        if target not in {"demo", "testnet", "live"}:
            raise ValueError("unsupported_account_target")
        if market not in {MarketType.SPOT.value, MarketType.FUTURES.value}:
            raise ValueError("unsupported_trading_market")
        if s.emergency_stop:
            raise ValueError("Cannot start: emergency stop is active.")
        if getattr(s, "bot_run_state", "stopped") in {"starting", "running", "analyzing", "executing", "waiting_data"}:
            raise ValueError("Cannot start duplicate run: bot is already active. Stop it first.")

        if target == "demo":
            if s.environment_mode != EnvironmentMode.DEMO.value:
                raise ValueError("demo_requires_demo_environment")
            checks = {
                "account_target": target, "environment_mode": s.environment_mode,
                "market_type": market, "public_market_data": True,
                "continuous_worker_available": True,
                "execution_scope": "internal_demo_shadow_only",
                "withdrawal_permissions": False,
            }
        else:
            if target == "live":
                if live_confirm is not True or not bool(getattr(s, "live_confirmed_by_user", False)):
                    raise ValueError("live_requires_explicit_user_confirmation")
            from app.services.binance_execution import binance_execution_service
            readiness = binance_execution_service.preflight(
                db, live_worker_ready=(target == "live"),
            )
            if not readiness.get("ready"):
                raise ValueError("binance_preflight_not_ready:" + str(readiness.get("error") or readiness.get("status")))
            checks = {
                "account_target": target, "environment_mode": s.environment_mode,
                "market_type": market, "binance_preflight": readiness,
                "risk_limits_present": bool(s.risk_limits),
                "withdrawal_permissions": False,
                "execution_scope": "supervised_binance_execution",
                "live_user_confirmation": bool(s.live_confirmed_by_user) if target == "live" else False,
            }

        # Atomic compare-and-set prevents concurrent API requests from creating
        # two supervised trading loops for the same persisted system state.
        claimed = db.execute(
            update(SystemState)
            .where(SystemState.id == 1, SystemState.bot_run_state == "stopped")
            .values(
                bot_run_state="starting",
                bot_started_at=_utcnow(),
                bot_stopped_at=None,
                bot_stop_reason="worker_start_requested",
                autonomous_enabled=(target != "demo"),
            )
        )
        if claimed.rowcount != 1:
            db.rollback()
            raise ValueError("Cannot start duplicate run: another worker claimed startup.")
        db.commit()
        db.refresh(s)
        log_event(
            db, action="master.start_requested",
            message=f"Supervised {target}/{market} worker startup requested.",
            details=checks, environment_mode=s.environment_mode, market_type=market,
        )
        db.commit()
        return {
            "ok": True, "state": self.get_state(db), "checks": checks,
            "note": f"Supervised {target}/{market} worker startup is pending supervisor confirmation.",
        }

    def stop_bot(self, db: Session, *, reason: str = "user_stop", close_positions: bool = False) -> dict[str, Any]:
        s = self._state(db)
        s.bot_run_state = "stopped"
        s.bot_stopped_at = _utcnow()
        s.bot_stop_reason = reason
        s.autonomous_enabled = False
        # A stop always revokes the prior Live approval; the next Live start
        # requires a fresh, explicit user confirmation.
        s.live_confirmed_by_user = False
        db.add(s)
        log_event(db, action="master.stop", severity="warning", message=f"Bot stopped: {reason}.", details={"close_positions_requested": bool(close_positions), "note": "Stops new actions/orders. Open positions are NOT auto-closed unless explicitly handled by a verified execution adapter."},
                  environment_mode=s.environment_mode, market_type=s.market_type)
        db.commit()
        return {"ok": True, "state": self.get_state(db), "close_positions_requested": bool(close_positions)}

    def engines(self, db: Session) -> list[dict[str, Any]]:
        self._state(db)
        rows = db.execute(select(EngineState).order_by(EngineState.engine_key)).scalars().all()
        return [{"key": r.engine_key, "name": r.name, "status": r.status, "message": r.message, "runtime_check": inspect_engine_runtime_contract(r.engine_key), "integration": ENGINE_INTEGRATION_MAP.get(r.engine_key, {"mode": "unmapped", "integration": "No verified runtime integration mapping", "decision_evidence": False, "authority": "none"}), "updated_at": r.updated_at.isoformat() if r.updated_at else None} for r in rows]

    def strategies(self) -> list[dict[str, Any]]:
        return list_strategies()

    @staticmethod
    def _build_strategy_context(symbol: str, market: MarketType, data_source: str) -> dict[str, Any]:
        """Build optional, provenance-checked public derivatives context for strategy candidates."""
        if market != MarketType.FUTURES or data_source != "binance_public":
            return {}
        try:
            from app.data.binance_futures_public import BinanceFuturesPublicData

            snapshot = BinanceFuturesPublicData().get_mark_price(symbol)
            raw_rate = snapshot.get("last_funding_rate")
            raw_stamp = snapshot.get("time")
            if isinstance(raw_rate, bool) or isinstance(raw_stamp, bool):
                return {}
            rate = float(raw_rate)
            stamp_ms = float(raw_stamp)
            now_ms = datetime.now(timezone.utc).timestamp() * 1000.0
            if (not math.isfinite(rate) or abs(rate) > 0.05
                    or not math.isfinite(stamp_ms) or stamp_ms <= 0
                    or now_ms - stamp_ms < -60_000 or now_ms - stamp_ms > 120_000):
                return {}
            published = datetime.fromtimestamp(stamp_ms / 1000.0, timezone.utc).isoformat()
            return {
                "funding_rate": {
                    "ok": True,
                    "source": "binance_futures_public_read_only",
                    "symbol": str(snapshot.get("symbol") or symbol).replace("/", "").upper(),
                    "fetched_at": published,
                    "funding_rate": rate,
                }
            }
        except Exception:
            # Missing public context must suppress the strategy, never block the
            # deterministic market/risk pipeline or invent a funding signal.
            return {}

    @staticmethod
    def _build_open_interest_strategy_context(symbol: str, market: MarketType, data_source: str) -> dict[str, Any]:
        """Build a fresh, closed-candle price/OI divergence candidate from public Futures data."""
        if market != MarketType.FUTURES or data_source != "binance_public":
            return {}
        try:
            from app.data.binance_futures_public import BinanceFuturesPublicData

            adapter = BinanceFuturesPublicData()
            history = adapter.get_open_interest_history(symbol, period="5m", limit=3)
            candles = adapter.get_klines(symbol, interval="5m", limit=3)
            if len(history) < 2 or len(candles) < 3:
                return {}
            previous, latest = history[-2], history[-1]
            # Ignore the currently-forming candle; compare the two latest closed candles.
            previous_candle, latest_candle = candles[-3], candles[-2]
            stamps = (int(latest["timestamp"]), int(latest_candle[6]))
            now_ms = datetime.now(timezone.utc).timestamp() * 1000.0
            if any(stamp <= 0 or now_ms - stamp < -60_000 or now_ms - stamp > 10 * 60_000 for stamp in stamps):
                return {}
            oi_previous = float(previous["open_interest"])
            oi_latest = float(latest["open_interest"])
            close_previous = float(previous_candle[4])
            close_latest = float(latest_candle[4])
            if not all(math.isfinite(value) and value > 0 for value in (
                oi_previous, oi_latest, close_previous, close_latest
            )):
                return {}
            oi_change = (oi_latest / oi_previous - 1.0) * 100.0
            price_change = (close_latest / close_previous - 1.0) * 100.0
            if not math.isfinite(oi_change) or not math.isfinite(price_change):
                return {}
            direction = "bearish" if price_change > 0 else "bullish" if price_change < 0 else "neutral"
            return {
                "open_interest": {
                    "ok": True,
                    "source": "binance_futures_public_read_only",
                    "symbol": str(latest.get("symbol") or symbol).replace("/", "").upper(),
                    "fetched_at": datetime.fromtimestamp(max(stamps) / 1000.0, timezone.utc).isoformat(),
                    "open_interest_change_pct": oi_change,
                    "price_change_pct": price_change,
                    "direction": direction,
                    "window": "two_latest_closed_5m_candles",
                }
            }
        except Exception:
            # A missing/stale OI snapshot suppresses only this candidate.
            return {}

    @staticmethod
    def _build_cvd_strategy_context(order_flow: dict[str, Any] | None) -> dict[str, Any]:
        """Expose a fresh, reconciled aggregate-trade delta as a bounded CVD candidate input."""
        if not isinstance(order_flow, dict) or order_flow.get("ok") is not True:
            return {}
        source = order_flow.get("source")
        if source not in {"binance_public_read_only", "binance_futures_public_read_only"}:
            return {}
        raw_delta = order_flow.get("taker_flow_delta_quote")
        if isinstance(raw_delta, bool):
            return {}
        try:
            delta = float(raw_delta)
            first = datetime.fromisoformat(str(order_flow["first_trade_time"]).replace("Z", "+00:00"))
            last = datetime.fromisoformat(str(order_flow["last_trade_time"]).replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            age = (now - last.astimezone(timezone.utc)).total_seconds()
            window = (last.astimezone(timezone.utc) - first.astimezone(timezone.utc)).total_seconds()
            if (not math.isfinite(delta) or first.tzinfo is None or last.tzinfo is None
                    or age < -5 or age > 120 or window < 0 or window > 120):
                return {}
        except (KeyError, TypeError, ValueError, OverflowError):
            return {}
        return {
            "cumulative_volume_delta": {
                "ok": True,
                "source": source,
                "symbol": order_flow.get("symbol"),
                "fetched_at": last.astimezone(timezone.utc).isoformat(),
                "first_trade_time": first.astimezone(timezone.utc).isoformat(),
                "last_trade_time": last.astimezone(timezone.utc).isoformat(),
                "cvd_change": delta,
                "window": "recent_public_aggregate_trades",
            }
        }

    @staticmethod
    def _validate_public_market_batches(market_batches: dict, expected_market: str = "spot") -> None:
        """Fail closed if any required public timeframe is missing, stale, or malformed."""
        required = {
            Timeframe.D1: 86400,
            Timeframe.H4: 14400,
            Timeframe.H1: 3600,
            Timeframe.M15: 900,
            Timeframe.M5: 300,
        }
        now = datetime.now(timezone.utc).timestamp()
        for timeframe, seconds in required.items():
            batch = market_batches.get(timeframe)
            if (batch is None
                    or getattr(getattr(batch, "source", None), "value", None) != "live_public"
                    or getattr(batch, "market_type", "spot") != expected_market):
                raise ValueError(f"verified_binance_{expected_market}_public_data_required:{timeframe.value}")
            if not batch.df or len(batch.df) < 30:
                raise ValueError(f"insufficient_public_market_data:{timeframe.value}")
            last = batch.df.rows[-1]
            stamp = last.get("close_time") or last.get("timestamp")
            try:
                if isinstance(stamp, bool) or stamp is None:
                    raise ValueError("missing_timestamp")
                if isinstance(stamp, (int, float)):
                    stamp_seconds = float(stamp) / 1000.0
                elif isinstance(stamp, str):
                    stamp_seconds = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                else:
                    raise ValueError("invalid_timestamp_type")
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"invalid_public_candle_timestamp:{timeframe.value}") from exc
            age = now - stamp_seconds
            allowed_age = max(300.0, float(seconds) * 2.0)
            if not math.isfinite(stamp_seconds) or age < -60.0 or age > allowed_age:
                raise ValueError(f"stale_or_future_public_market_data:{timeframe.value}")

    def analyze(self, db: Session, *, symbol: str, market_type: str, environment_mode: str, balance: float = 1000.0, max_leverage: float = 3.0, risk_per_trade: float = 0.01, data_source: str = "synthetic", market_batches: dict | None = None) -> dict[str, Any]:
        self.ensure_bootstrap(db)
        market = MarketType(market_type)
        env = EnvironmentMode(environment_mode)
        # Analysis is read-only and may run on a Live account even when the
        # deployment has not enabled real order execution. The separate
        # supervised Live worker enforces the execution setting and user approval.
        if market_batches is not None:
            batches = market_batches
        elif data_source == "binance_public":
            public_market = "futures" if market == MarketType.FUTURES else "spot"
            batches = self.market_data.get_public_multi_timeframe(symbol, market_type=public_market)
        elif data_source == "synthetic":
            batches = self.market_data.get_multi_timeframe(symbol, seed=42, market_type=market.value)
        else:
            raise ValueError("unsupported_data_source")
        if data_source == "binance_public":
            public_market = "futures" if market == MarketType.FUTURES else "spot"
            self._validate_public_market_batches(batches, expected_market=public_market)
        analyses = {tf.value: self.technical.analyze(batch.df, tf) for tf, batch in batches.items()}
        # Portfolio-wide gross exposure and position count are evaluated before
        # every proposed entry, not independently per symbol.
        open_positions = db.execute(select(PositionRecord).where(
            PositionRecord.environment_mode == env.value,
            PositionRecord.market_type == market.value,
            PositionRecord.status == "open",
        )).scalars().all()
        current_exposure = sum(
            abs(float(p.mark_price or p.entry_avg_price or 0.0) * float(p.quantity or 0.0))
            for p in open_positions
        )
        try:
            balance_value = float(balance)
            exposure_multiple = float(settings.max_portfolio_exposure_multiple)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("invalid_portfolio_exposure_configuration")
        if (not math.isfinite(balance_value) or balance_value <= 0
                or not math.isfinite(exposure_multiple) or exposure_multiple <= 0):
            raise ValueError("invalid_portfolio_exposure_configuration")
        # Keep advisory sizing aligned with the independent Shadow execution
        # ceiling; leverage must not multiply the permitted gross portfolio cap.
        max_portfolio_exposure = balance_value * exposure_multiple
        state = db.get(SystemState, 1)
        raw_limits = getattr(state, "risk_limits", None) if state else None
        if raw_limits is not None and not isinstance(raw_limits, dict):
            max_open_positions = 0
        else:
            risk_limits = raw_limits or {}
            configured_positions = risk_limits.get(
                "max_open_positions_cap", settings.max_open_positions_cap,
            )
            if type(configured_positions) is not int:
                max_open_positions = 0
            else:
                max_open_positions = max(0, min(
                    configured_positions, settings.max_open_positions_cap,
                ))
        microstructure_analysis = {"status": "not_available"}
        if data_source == "binance_public" and market in {MarketType.SPOT, MarketType.FUTURES}:
            public_market = "futures" if market == MarketType.FUTURES else "spot"
            liquidity = LiquidityAnalysisEngine().fetch_and_analyze(
                symbol, limit=100, market_type=public_market,
            )
            order_flow = OrderFlowEngine().fetch_and_analyze(
                symbol, limit=500, market_type=public_market,
            )
            microstructure_analysis = {
                "liquidity": liquidity,
                "order_flow": order_flow,
                "status": "available" if liquidity.get("ok") and order_flow.get("ok") else "partial_or_unavailable",
            }
        # News is attached as contextual evidence only. It cannot directly
        # create or modify a buy/sell decision; the quantitative and risk
        # engines remain authoritative. Prefer the base-asset ticker.
        from app.engines.news import NewsAnalysisEngine
        news_context = NewsAnalysisEngine().fetch_and_analyze(
            keyword=(symbol.split("/", 1)[0] if isinstance(symbol, str) else None),
            limit=50,
        )
        if not isinstance(news_context, dict):
            news_context = {"ok": False, "error": "news_context_invalid"}
        from app.learning.outcomes import build_strategy_memory_context
        strategy_memory = build_strategy_memory_context(db)
        strategy_context = self._build_strategy_context(symbol, market, data_source)
        strategy_context.update(self._build_cvd_strategy_context(order_flow if microstructure_analysis.get("status") != "not_available" else None))
        strategy_context.update(self._build_open_interest_strategy_context(symbol, market, data_source))
        result = self.decision.decide(
            symbol=symbol, market=market, environment=env,
            timeframe_analyses={Timeframe(k): v for k, v in analyses.items()},
            balance=balance, max_leverage=max_leverage, risk_per_trade=risk_per_trade,
            batches=batches, microstructure_analysis=microstructure_analysis,
            open_positions=len(open_positions), current_exposure=current_exposure,
            max_exposure=max_portfolio_exposure, max_open_positions=max_open_positions,
            news_context=news_context, strategy_memory=strategy_memory,
            strategy_context=strategy_context,
        )
        # Optional external LLM review is strictly advisory and runs only when an
        # enabled provider is configured. It cannot alter the deterministic action
        # or the independent RiskEngine result.
        from app.integrations.ai_providers import evaluate_decision_advisory
        timeframe_evidence = {}
        for timeframe_key, analysis in analyses.items():
            indicators = getattr(analysis, "indicators", {}) or {}
            structure = getattr(analysis, "structure", {}) or {}
            candle_evidence = getattr(analysis, "candles", {}) or {}
            timeframe_evidence[timeframe_key] = {
                "trend": getattr(analysis, "trend", "unknown"),
                "signals": list(getattr(analysis, "signals", []) or [])[:40],
                "indicators": dict(list(indicators.items())[:40]) if isinstance(indicators, dict) else {},
                "structure": {
                    key: (value[:6] if isinstance(value, list) else value)
                    for key, value in structure.items()
                    if key in {"bias", "bos_choch", "breakouts", "liquidity_sweeps",
                               "fair_value_gaps", "support_resistance", "market_regime",
                               "order_blocks", "premium_discount"}
                } if isinstance(structure, dict) else {},
                "candles": {
                    key: value for key, value in candle_evidence.items()
                    if key in {"patterns", "close_strength", "volume_spike", "last_closed_timestamp"}
                } if isinstance(candle_evidence, dict) else {},
            }
        result["news_analysis"] = news_context
        result["ai_evaluation"] = evaluate_decision_advisory(db, {
            "symbol": symbol,
            "market": market.value,
            "environment": env.value,
            "data_source": result.get("data_source", "unknown"),
            "decision": {
                "action": result.get("action", "wait"),
                "reason": result.get("reason"),
                "confidence_score": result.get("confidence"),
                "strategy": result.get("strategy"),
                "entry_price": result.get("entry_price"),
                "stop_loss": result.get("stop_loss"),
                "take_profit": result.get("take_profit"),
                "leverage": result.get("leverage"),
            },
            "timeframe_evidence": timeframe_evidence,
            "historical_strategy_memory": strategy_memory,
            "multi_agent_review": result.get("multi_agent_deliberation"),
            "microstructure_evidence": microstructure_analysis,
            "news_context": {
                "status": "available" if news_context.get("ok") else "unavailable",
                "items": news_context.get("items", [])[:20],
                "source": news_context.get("source"),
                "note": "News is contextual evidence only; it cannot issue or override a trading action.",
            },
            "independent_risk_result": result.get("risk"),
            "system_state": {
                "emergency_stop": bool(getattr(state, "emergency_stop", True)),
                "risk_limits": raw_limits if isinstance(raw_limits, dict) else {},
            },
            "portfolio": {
                "open_positions": len(open_positions),
                "current_gross_exposure": current_exposure,
                "maximum_gross_exposure": max_portfolio_exposure,
                "maximum_open_positions": max_open_positions,
            },
        })
        rec = DecisionRecord(
            environment_mode=env.value, market_type=market.value, symbol=symbol,
            action=result.get("action", "wait"), strategy=result.get("strategy"),
            confidence=result.get("confidence"), entry_price=result.get("entry_price"),
            stop_loss=result.get("stop_loss"), take_profit=result.get("take_profit"),
            risk_reward=result.get("risk_reward"), leverage=result.get("leverage"),
            rationale=result.get("reason", ""), timeframe_context=result.get("timeframes"),
            indicators={
                "per_timeframe_last": {k: v.indicators for k, v in analyses.items()},
                "microstructure": result.get("microstructure_analysis"),
                "ai_evaluation": result.get("ai_evaluation"),
            },
            costs=result.get("costs"), news_context=news_context, data_source=result.get("data_source", "synthetic_labeled"),
            reproducibility={"app_version": "0.1.0-foundation", "inputs": {"symbol": symbol, "market_type": market.value, "environment_mode": env.value, "balance": balance, "max_leverage": max_leverage, "risk_per_trade": risk_per_trade},
                              "timeframes": [t.value for t in batches.keys()], "data_labels": {t.value: b.source.value for t, b in batches.items()},
                              "note": "Replay requires the same market-data provider, timestamps, configuration, and analysis version."},
        )
        db.add(rec)
        log_event(db, action="analysis.decision", message=f"Decision {rec.action} for {symbol} ({env.value}/{market.value}).",
                  environment_mode=env.value, market_type=market.value, entity_type="decision", details={"symbol": symbol, "action": rec.action, "confidence": rec.confidence})
        db.commit(); db.refresh(rec)
        result["decision_id"] = rec.id
        result["recorded"] = True
        return result

    def get_engines_detailed(self, db: Session) -> list[dict[str, Any]]:
        self._state(db)
        rows = db.execute(select(EngineState).order_by(EngineState.engine_key)).scalars().all()
        result = []
        for r in rows:
            result.append({
                "key": r.engine_key,
                "name": r.name,
                "status": r.status,
                "message": (
                    "Binance execution service and supervised worker are implemented; worker start/stop is managed by the main trading control and requires configured exchange credentials."
                    if r.engine_key == "trade_execution" else
                    (r.message or "Monitoring is available; independent engine lifecycle controls are not implemented.")
                ),
                "details": {
                    **(r.details or {}),
                    "runtime_check": inspect_engine_runtime_contract(r.engine_key),
                    "lifecycle_control": "managed_by_trading_supervisor" if r.engine_key == "trade_execution" else "not_implemented",
                    "integration": ENGINE_INTEGRATION_MAP.get(r.engine_key, {
                        "mode": "unmapped", "integration": "No verified runtime integration mapping",
                        "decision_evidence": False, "authority": "none",
                    }),
                },
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "can_start_stop": False,
                "controls": []
            })
        return result

    def control_engine(self, db: Session, key: str, action: str, user: str = "system") -> dict[str, Any]:
        from .audit import log_event
        es = db.execute(select(EngineState).where(EngineState.engine_key == key)).scalar_one_or_none()
        if not es:
            return {"ok": False, "error": "engine_not_found"}
        action = (action or "").lower()
        if action in {"start", "stop", "restart"}:
            # Registry status is metadata, not a process supervisor. Do not
            # claim an engine was started/stopped when no runtime adapter exists.
            return {
                "ok": False,
                "error": "engine_lifecycle_not_implemented",
                "engine": key,
                "requested_action": action,
                "current_status": es.status,
            }
        if action == "test":
            # A status write is not an engine self-test. Fail honestly until a
            # real, isolated self-test is implemented for the selected engine.
            return {
                "ok": False,
                "error": "engine_self_test_not_implemented",
                "engine": key,
                "current_status": es.status,
            }
        return {"ok": False, "error": "unknown_action"}



core_service = TitanCoreService()

