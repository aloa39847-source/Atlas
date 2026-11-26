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


def _run_deterministic_shadow_round_trip(db, service, *, market: str, symbol: str,
                                         side: str, entry: float, exit_price: float,
                                         quantity: float, leverage: float = 1.0) -> dict:
    """Exercise the real Shadow order/position/close path with deterministic CI prices."""
    opened = service.create_shadow_order(
        db, env="demo", market=market, symbol=symbol, side=side, order_type="market",
        quantity=quantity, price=entry, reference_price=entry, mark_price=entry,
        idempotency_key=f"smoke-{market}-{side}-{symbol.replace('/', '')}-open",
        simulate="fill", fill_ratio=1.0, fee_rate=0.001, leverage=leverage,
    )
    if not opened.ok or opened.order is None or opened.position is None:
        raise RuntimeError(f"shadow_open_failed:{market}:{side}:{opened.message}:{opened.details}")
    db.commit()

    position_id = int(opened.position.id)
    closed = service.close_shadow_position(db, position_id=position_id, close_price=exit_price, fee_rate=0.001)
    if not closed.get("ok"):
        raise RuntimeError(f"shadow_close_failed:{market}:{side}:{closed}")
    db.commit()

    from app.models.entities import PositionRecord
    position = db.get(PositionRecord, position_id)
    if position is None or position.status != "closed" or float(position.quantity) != 0.0:
        raise RuntimeError(f"shadow_position_not_flattened:{market}:{side}:{position_id}")
    return {
        "market": market, "symbol": symbol, "side": side, "entry_price": entry,
        "exit_price": exit_price, "quantity": quantity, "leverage": leverage,
        "open_order_id": opened.order.id, "close_order_id": closed.get("order_id"),
        "position_id": position_id, "status": position.status,
        "realized_pnl": position.realized_pnl,
        "open_order_shadow": opened.order.shadow,
    }


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

        shadow = ShadowTradingService()
        state.market_type = "spot"
        db.add(state)
        db.commit()
        round_trips = [
            _run_deterministic_shadow_round_trip(
                db, shadow, market="spot", symbol="BTC/USDT", side="buy",
                entry=50_000.0, exit_price=50_100.0, quantity=0.01,
            ),
        ]

        # Futures is exercised independently for both directions. These are
        # deterministic internal Shadow fills, not exchange/testnet orders.
        state.market_type = "futures"
        db.add(state)
        db.commit()
        round_trips.extend([
            _run_deterministic_shadow_round_trip(
                db, shadow, market="futures", symbol="ETH/USDT", side="buy",
                entry=2_000.0, exit_price=2_010.0, quantity=0.05, leverage=2.0,
            ),
            _run_deterministic_shadow_round_trip(
                db, shadow, market="futures", symbol="BNB/USDT", side="sell",
                entry=600.0, exit_price=595.0, quantity=0.1, leverage=2.0,
            ),
        ])
        futures_portfolio = shadow.get_portfolio(db, env="demo", market="futures")
        if futures_portfolio["snapshot"]["positions_open"] != 0:
            raise RuntimeError("futures_smoke_left_open_positions")

        state.market_type = "spot"
        db.add(state)
        db.commit()
        portfolio = shadow.get_portfolio(db, env="demo", market="spot")
        if portfolio["snapshot"]["positions_open"] != 0:
            raise RuntimeError("spot_smoke_left_open_positions")
        if len(round_trips) != 3 or not all(item["status"] == "closed" for item in round_trips):
            raise RuntimeError("shadow_round_trip_assertion_failed")

        report = {
            "test": "isolated_demo_shadow_full_cycle_smoke",
            "starting_balance_usdt": TEST_BALANCE,
            "risk_per_trade": 0.005,
            "symbols_tested": TEST_SYMBOLS,
            "market_analysis_cycles": cycles,
            "deterministic_shadow_round_trips": round_trips,
            "spot_portfolio": portfolio["snapshot"],
            "spot_open_positions": portfolio["positions"],
            "spot_recent_orders": portfolio["orders"],
            "futures_portfolio": futures_portfolio["snapshot"],
            "futures_open_positions": futures_portfolio["positions"],
            "futures_recent_orders": futures_portfolio["orders"],
            "expected_round_trips": 3,
            "completed_round_trips": len(round_trips),
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
