"""Run an isolated 10,000 USDT internal Spot Demo/Shadow readiness pass.

Uses Binance Public read-only market data. No API keys, private account access,
or exchange orders are used. The larger balance exists only in this ephemeral
CI database and does not change the user's normal Demo balance.
"""
from __future__ import annotations

import json

from app.models import db as dbmod
from app.models.entities import SystemState
from app.services.core_service import core_service
from app.services.orchestrator import TradingOrchestrator
from app.services.shadow_trading import ShadowTradingService

TEST_BALANCE = 10_000.0
TEST_SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT"]


def main() -> int:
    dbmod.init_db()
    db = dbmod.SessionLocal()
    try:
        core_service.ensure_bootstrap(db)
        state = db.get(SystemState, 1)
        if state is None:
            raise RuntimeError("system_state_missing")
        if state.emergency_stop:
            raise RuntimeError("emergency_stop_active")
        if state.environment_mode != "demo" or state.market_type != "spot":
            raise RuntimeError("smoke_requires_demo_spot")
        limits = dict(state.risk_limits or {})
        limits["demo_initial_balance"] = TEST_BALANCE
        limits["demo_risk_per_trade"] = 0.005
        state.risk_limits = limits
        db.add(state)
        db.commit()

        orchestrator = TradingOrchestrator()
        cycles = []
        for symbol in TEST_SYMBOLS:
            result = orchestrator.run_cycle(
                db, symbol=symbol, market="spot", environment="demo", execute_shadow=True,
                max_data_age_seconds=300,
            )
            db.commit()
            cycles.append({
                "symbol": symbol,
                "ok": result.get("ok"),
                "action": result.get("analysis_action"),
                "executed": result.get("executed", False),
                "execution_mode": result.get("execution_mode", "none"),
                "error": result.get("error"),
                "data_source": result.get("data_source"),
                "risk_budget": result.get("risk_budget"),
            })
            if not result.get("ok"):
                print(json.dumps({"failed_symbol": symbol, "result": result}, ensure_ascii=False, default=str))
                return 2
            if result.get("data_source") not in ("binance_public", None):
                print(json.dumps({"error": "unexpected_data_source", "result": result}, ensure_ascii=False, default=str))
                return 3

        portfolio = ShadowTradingService().get_portfolio(db, env="demo", market="spot")
        report = {
            "test": "isolated_spot_demo_shadow_readiness",
            "starting_balance_usdt": TEST_BALANCE,
            "risk_per_trade": 0.005,
            "symbols_tested": TEST_SYMBOLS,
            "cycles": cycles,
            "portfolio": portfolio["snapshot"],
            "open_positions": portfolio["positions"],
            "recent_orders": portfolio["orders"],
            "exchange_orders_sent": False,
            "private_binance_account_accessed": False,
            "database_scope": "ephemeral_ci_only",
        }
        print(json.dumps(report, ensure_ascii=False, default=str))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
