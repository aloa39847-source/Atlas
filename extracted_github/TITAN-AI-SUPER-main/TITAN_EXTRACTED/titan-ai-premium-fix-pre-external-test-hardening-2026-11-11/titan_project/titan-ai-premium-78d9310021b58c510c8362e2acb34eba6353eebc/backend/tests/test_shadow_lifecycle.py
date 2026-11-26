from __future__ import annotations

from app.models import db as dbmod
from app.models.entities import OrderRecord, PositionRecord, PortfolioSnapshot, SystemState
from app.services.shadow_trading import ShadowTradingService
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _db(tmp_path):
    db_path = tmp_path / "shadow.db"
    dbmod.engine.dispose()
    dbmod.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, future=True)
    dbmod.SessionLocal = sessionmaker(bind=dbmod.engine, autoflush=False, autocommit=False, future=True)
    dbmod.Base.metadata.create_all(bind=dbmod.engine)
    return dbmod.SessionLocal()


def test_shadow_order_requires_stable_idempotency_key(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        invalid_keys = (
            (None, "idempotency_key_required"),
            ("", "idempotency_key_invalid_length"),
            ("weak", "idempotency_key_invalid_length"),
            ("x" * 129, "idempotency_key_invalid_length"),
        )
        for key, expected_reason in invalid_keys:
            result = svc.create_shadow_order(
                db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
                order_type="limit", quantity=0.001, price=50000,
                reference_price=50000, idempotency_key=key,
            )
            assert not result.ok
            assert result.message == expected_reason
            assert result.order is None
        assert svc.get_portfolio(db, env="demo", market="spot")["orders"] == []
    finally:
        db.close()


def test_invalid_demo_equity_configuration_never_restores_default_cash(tmp_path):
    db = _db(tmp_path)
    try:
        state = SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="spot",
            risk_limits={"demo_initial_balance": 0.0},
        )
        db.add(state)
        db.commit()
        service = ShadowTradingService()
        for invalid in (0.0, -1.0, float("nan"), float("inf"), True, "invalid"):
            state.risk_limits = {"demo_initial_balance": invalid}
            db.add(state)
            db.commit()
            assert service._initial_cash(db, env="demo") == 0.0
        state.risk_limits = []
        db.add(state)
        db.commit()
        assert service._initial_cash(db, env="demo") == 0.0
        blocked = service.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.001, price=50000,
            reference_price=50000, idempotency_key="malformed-risk-limits-01",
        )
        assert not blocked.ok
        assert blocked.message == "daily_loss_limit_reached"
        assert blocked.order is None
    finally:
        db.close()


def test_zero_open_position_limit_blocks_all_new_entries(tmp_path):
    db = _db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="spot",
            bot_run_state="stopped", autonomous_enabled=False, emergency_stop=False,
            risk_limits={"max_open_positions_cap": 0},
        ))
        db.commit()
        result = ShadowTradingService().create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.001, price=50000,
            reference_price=50000, idempotency_key="zero-position-cap-001",
        )
        assert not result.ok
        assert result.message == "max_open_positions_reached"
        assert result.order is None
        assert db.query(OrderRecord).count() == 0
    finally:
        db.close()


def test_zero_open_position_limit_blocks_adding_to_existing_symbol_position(tmp_path):
    db = _db(tmp_path)
    try:
        state = SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="spot",
            bot_run_state="stopped", autonomous_enabled=False, emergency_stop=False,
            risk_limits={"max_open_positions_cap": 10},
        )
        db.add(state)
        db.commit()
        service = ShadowTradingService()
        opened = service.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.001, price=50000,
            reference_price=50000, idempotency_key="zero-cap-existing-open",
        )
        assert opened.ok
        db.commit()
        state.risk_limits = {"max_open_positions_cap": 0}
        db.add(state)
        db.commit()

        blocked = service.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.0005, price=50000,
            reference_price=50000, idempotency_key="zero-cap-existing-add",
        )
        assert not blocked.ok
        assert blocked.message == "max_open_positions_reached"
        assert db.query(OrderRecord).count() == 1
        assert db.get(PositionRecord, opened.position.id).quantity == opened.position.quantity
    finally:
        db.close()


def test_malformed_open_position_limits_fail_closed(tmp_path):
    db = _db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="spot",
            bot_run_state="stopped", autonomous_enabled=False, emergency_stop=False,
            risk_limits={"max_open_positions_cap": True},
        ))
        db.commit()
        state = db.get(SystemState, 1)
        for index, invalid_value in enumerate((True, 1.5, "10", None, -1)):
            state.risk_limits = {"max_open_positions_cap": invalid_value}
            db.add(state)
            db.commit()
            result = ShadowTradingService().create_shadow_order(
                db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
                order_type="limit", quantity=0.001, price=50000,
                reference_price=50000, idempotency_key=f"bad-position-cap-{index:02d}",
            )
            assert not result.ok
            assert result.message == "max_open_positions_reached"
            assert result.order is None
        assert db.query(OrderRecord).count() == 0
    finally:
        db.close()


def test_shadow_full_fill_and_portfolio(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        r = svc.create_shadow_order(db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
                                    quantity=0.001, price=50000, reference_price=50000, idempotency_key="shadow-key-001", simulate="fill", fill_ratio=1.0)
        db.commit()
        assert r.ok and r.order.status == "filled" and r.order.fees_paid > 0
        p = svc.get_portfolio(db, env="demo", market="spot")
        assert p["snapshot"]["positions_open"] == 1
        assert p["positions"][0]["quantity"] == 0.001
        assert p["snapshot"]["fees_total"] > 0
    finally:
        db.close()


def test_partial_fill(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        r = svc.create_shadow_order(db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
                                    quantity=0.7, price=100, reference_price=100, idempotency_key="shadow-key-002", simulate="fill", fill_ratio=0.4)
        db.commit()
        assert r.ok and r.order.status == "partially_filled" and round(r.order.filled_quantity, 8) == 0.28
    finally:
        db.close()


def test_cancel(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        r = svc.create_shadow_order(db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
                                    quantity=0.7, price=100, reference_price=100, idempotency_key="shadow-key-003", simulate="cancel")
        db.commit()
        assert r.ok and r.order.status == "cancelled"
    finally:
        db.close()


def test_duplicate_idempotency_no_duplicate_order(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        r1 = svc.create_shadow_order(db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
                                     quantity=0.7, price=100, reference_price=100, idempotency_key="shadow-dup-0001", simulate="fill")
        db.commit()
        r2 = svc.create_shadow_order(db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
                                     quantity=1.0, price=100, reference_price=100, idempotency_key="shadow-dup-0001", simulate="fill")
        db.commit()
        assert r1.ok and r2.ok
        assert r2.details.get("duplicate") is True
        assert r2.order.id == r1.order.id
        p = svc.get_portfolio(db, env="demo", market="spot")
        assert len(p["orders"]) == 1  # no duplicate
    finally:
        db.close()


def test_connection_loss_recovery_requires_reconciliation(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        r = svc.create_shadow_order(db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
                                    quantity=0.7, price=100, reference_price=100, idempotency_key="shadow-loss-001", simulate="connection_loss")
        db.commit()
        assert r.ok and r.details.get("reconcile_required") is True
        # retry with same idempotency key returns existing (no duplicate)
        r2 = svc.create_shadow_order(db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
                                     quantity=0.7, price=100, reference_price=100, idempotency_key="shadow-loss-001", simulate="fill")
        db.commit()
        assert r2.details.get("duplicate") is True
    finally:
        db.close()


def test_emergency_stop_blocks_new_shadow_orders(tmp_path):
    db = _db(tmp_path)
    try:
        db.add(SystemState(id=1, environment_mode="demo", account_target="demo", market_type="spot",
                           bot_run_state="stopped", autonomous_enabled=False, emergency_stop=True))
        db.commit()
        svc = ShadowTradingService()
        result = svc.create_shadow_order(db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
                                         order_type="limit", quantity=0.01, price=50000,
                                         reference_price=50000, idempotency_key="emergency-stop-001")
        assert not result.ok
        assert result.message == "emergency_stop_active"
        assert result.order is None
        assert svc.get_portfolio(db, env="demo", market="spot")["orders"] == []
    finally:
        db.close()


def test_spot_sell_reduces_position_and_uses_single_fee_accounting(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        buy = svc.create_shadow_order(db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
                                      quantity=0.1, price=100, reference_price=100, idempotency_key="spot-buy-account-01", fee_rate=0.001)
        db.commit()
        assert buy.ok and buy.position.quantity == 0.1

        sell = svc.create_shadow_order(db, env="demo", market="spot", symbol="BTC/USDT", side="sell", order_type="limit",
                                       quantity=0.04, price=110, reference_price=110, idempotency_key="spot-sell-account-01", fee_rate=0.001)
        db.commit()
        assert sell.ok and round(sell.position.quantity, 8) == 0.06
        assert round(sell.position.realized_pnl, 8) == 0.4

        portfolio = svc.get_portfolio(db, env="demo", market="spot")
        assert round(portfolio["snapshot"]["cash_balance"], 8) == 144.3856
        assert round(portfolio["snapshot"]["equity_estimate"], 8) == 150.9856
        assert round(portfolio["snapshot"]["fees_total"], 8) == 0.0144
    finally:
        db.close()


def test_spot_sell_cannot_create_short_position(tmp_path):
    db = _db(tmp_path)
    try:
        result = ShadowTradingService().create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="sell", order_type="limit",
            quantity=0.01, price=100, reference_price=100, idempotency_key="spot-sell-no-inventory-01"
        )
        assert not result.ok
        assert result.message == "insufficient_demo_base_asset"
    finally:
        db.close()


def test_spot_buy_cannot_exceed_demo_quote_balance(tmp_path):
    db = _db(tmp_path)
    try:
        db.add(SystemState(id=1, environment_mode="demo", account_target="demo", market_type="spot",
                           risk_limits={"demo_initial_balance": 50.0}))
        db.commit()
        result = ShadowTradingService().create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
            quantity=0.7, price=100, reference_price=100, idempotency_key="spot-buy-overbalance-01"
        )
        assert not result.ok
        assert result.message == "insufficient_demo_quote_balance"
    finally:
        db.close()


def test_close_shadow_position_records_sell_and_updates_equity(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        buy = svc.create_shadow_order(db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
                                      quantity=0.1, price=100, reference_price=100, idempotency_key="spot-close-buy-01")
        db.commit()
        result = svc.close_shadow_position(db, position_id=buy.position.id, close_price=105)
        db.commit()
        assert result["ok"] is True
        portfolio = svc.get_portfolio(db, env="demo", market="spot")
        assert portfolio["snapshot"]["positions_open"] == 0
        assert len(portfolio["orders"]) == 2
        assert portfolio["orders"][0]["side"] == "sell"
        assert round(portfolio["snapshot"]["equity_estimate"], 8) == 150.0 + 0.5 - 0.0205
    finally:
        db.close()


def test_futures_shadow_execution_is_simulated_and_leverage_bounded(tmp_path):
    db = _db(tmp_path)
    try:
        service = ShadowTradingService()
        result = service.create_shadow_order(
            db, env="demo", market="futures", symbol="BTC/USDT", side="buy", order_type="market",
            quantity=0.01, price=100, reference_price=100,
            idempotency_key="futures-shadow-simulation-001", leverage=2,
        )
        assert result.ok
        assert result.order.shadow is True
        assert result.order.market_type == "futures"
        assert result.position.side == "long"
        assert result.position.metadata_json["leverage"] == 2
        assert result.position.leverage == 2
        assert result.order.metadata_json["simulate"] == "fill"

        too_large = service.create_shadow_order(
            db, env="demo", market="futures", symbol="BTC/USDT", side="buy", order_type="market",
            quantity=8.0, price=100, reference_price=100,
            idempotency_key="futures-shadow-over-cap-001", leverage=2,
        )
        assert not too_large.ok
        assert too_large.message == "notional_exceeds_limit"

        spot_leverage = service.create_shadow_order(
            db, env="demo", market="spot", symbol="ETH/USDT", side="buy", order_type="market",
            quantity=0.01, price=100, reference_price=100,
            idempotency_key="spot-shadow-leverage-denied-001", leverage=2,
        )
        assert not spot_leverage.ok
        assert spot_leverage.message == "spot_leverage_not_supported"
    finally:
        db.close()


def test_demo_shadow_uses_approved_initial_balance_default(tmp_path):
    db = _db(tmp_path)
    try:
        portfolio = ShadowTradingService().get_portfolio(db, env="demo", market="spot")
        assert portfolio["snapshot"]["cash_balance"] == 150.0
        assert portfolio["snapshot"]["equity_estimate"] == 150.0
    finally:
        db.close()


def test_daily_realized_loss_limit_pauses_new_entries_but_allows_reducing_sell(tmp_path):
    db = _db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="spot",
            bot_run_state="running", autonomous_enabled=False, emergency_stop=False,
            risk_limits={"demo_initial_balance": 150.0, "max_daily_loss_cap": 0.10},
        ))
        db.commit()
        svc = ShadowTradingService()
        buy = svc.create_shadow_order(db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.7, price=100, reference_price=100,
            idempotency_key="daily-loss-buy-001", fee_rate=0.001)
        db.commit()
        assert buy.ok

        first_sell = svc.create_shadow_order(db, env="demo", market="spot", symbol="BTC/USDT", side="sell",
            order_type="limit", quantity=0.35, price=78, reference_price=78,
            idempotency_key="daily-loss-sell-001", fee_rate=0.001)
        db.commit()
        assert first_sell.ok
        assert first_sell.order.metadata_json["realized_pnl_net"] < 0
        assert round(first_sell.order.metadata_json["entry_fee_allocated"], 8) == 0.035

        # Risk-reducing sells remain available to flatten the open Spot inventory.
        second_sell = svc.create_shadow_order(db, env="demo", market="spot", symbol="BTC/USDT", side="sell",
            order_type="limit", quantity=0.35, price=78, reference_price=78,
            idempotency_key="daily-loss-sell-002", fee_rate=0.001)
        db.commit()
        assert second_sell.ok
        assert second_sell.order.metadata_json["realized_pnl_net"] + first_sell.order.metadata_json["realized_pnl_net"] < -15.0

        blocked = svc.create_shadow_order(db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.01, price=80, reference_price=80,
            idempotency_key="daily-loss-buy-002", fee_rate=0.001)
        assert not blocked.ok
        assert blocked.message == "daily_loss_limit_reached"
        state = db.get(SystemState, 1)
        assert state.bot_run_state == "stopped"
        assert state.autonomous_enabled is False
    finally:
        db.close()


def test_daily_loss_guard_includes_marked_unrealized_losses(tmp_path):
    db = _db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="spot",
            bot_run_state="running", autonomous_enabled=False, emergency_stop=False,
            risk_limits={"demo_initial_balance": 150.0, "max_daily_loss_cap": 0.10},
        ))
        db.add(PositionRecord(
            environment_mode="demo", market_type="spot", symbol="BTC/USDT", side="long_spot",
            quantity=1.0, entry_avg_price=100.0, mark_price=100.0, unrealized_pnl=0.0,
            realized_pnl=0.0, fees_total=0.0, status="open", shadow=True, leverage=1.0,
        ))
        db.commit()
        svc = ShadowTradingService()
        assert svc.update_mark_prices(db, env="demo", market="spot", prices={"BTC/USDT": 80.0}) == 1
        db.flush()
        position = db.query(PositionRecord).filter_by(symbol="BTC/USDT", status="open").one()
        assert position.mark_price == 80.0
        assert position.unrealized_pnl == -20.0

        blocked = svc.create_shadow_order(
            db, env="demo", market="spot", symbol="ETH/USDT", side="buy", order_type="limit",
            quantity=0.01, price=80.0, reference_price=80.0,
            idempotency_key="daily-floating-loss-buy-01",
        )
        assert not blocked.ok
        assert blocked.message == "daily_loss_limit_reached"
        assert blocked.details["daily_realized_plus_unrealized_pnl"] == -20.0
        state = db.get(SystemState, 1)
        assert state.bot_run_state == "stopped"
    finally:
        db.close()


def test_persistent_stop_loss_closes_shadow_position(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        buy = svc.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
            quantity=0.1, price=100, reference_price=100, idempotency_key="protect-stop-buy-01",
        )
        db.commit()
        assert buy.ok
        configured = svc.set_position_protection(db, position_id=buy.position.id, stop_loss=95.0, take_profit=115.0)
        db.commit()
        assert configured["ok"] is True

        exits = svc.evaluate_position_protection(db, env="demo", market="spot", prices={"BTC/USDT": 94.0})
        db.commit()
        assert len(exits) == 1
        assert exits[0]["reason"] == "stop_loss"
        assert exits[0]["ok"] is True
        portfolio = svc.get_portfolio(db, env="demo", market="spot")
        assert portfolio["snapshot"]["positions_open"] == 0
    finally:
        db.close()


def test_trailing_stop_ratchets_up_and_closes_on_reversal(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        buy = svc.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
            quantity=0.1, price=100, reference_price=100, idempotency_key="protect-trail-buy-01",
        )
        db.commit()
        configured = svc.set_position_protection(db, position_id=buy.position.id, trailing_stop_pct=0.05)
        db.commit()
        assert configured["ok"] is True

        assert svc.evaluate_position_protection(db, env="demo", market="spot", prices={"BTC/USDT": 110.0}) == []
        position = db.get(type(buy.position), buy.position.id)
        assert position.metadata_json["highest_price"] == 110.0
        assert round(position.metadata_json["trailing_stop_price"], 8) == 104.5

        exits = svc.evaluate_position_protection(db, env="demo", market="spot", prices={"BTC/USDT": 104.0})
        db.commit()
        assert len(exits) == 1
        assert exits[0]["reason"] == "trailing_stop"
        assert exits[0]["ok"] is True
    finally:
        db.close()


def test_position_protection_rejects_invalid_long_stop(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        buy = svc.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
            quantity=0.1, price=100, reference_price=100, idempotency_key="protect-invalid-buy-01",
        )
        db.commit()
        result = svc.set_position_protection(db, position_id=buy.position.id, stop_loss=101.0)
        assert result["ok"] is False
        assert result["error"] == "stop_loss_on_loss_side_of_entry_required"
    finally:
        db.close()


def test_shadow_entry_enforces_portfolio_open_position_cap(tmp_path):
    db = _db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="spot",
            bot_run_state="running", autonomous_enabled=False, emergency_stop=False,
            risk_limits={"demo_initial_balance": 150.0, "max_open_positions_cap": 2},
        ))
        db.commit()
        svc = ShadowTradingService()
        for symbol in ("BTC/USDT", "ETH/USDT"):
            result = svc.create_shadow_order(
                db, env="demo", market="spot", symbol=symbol, side="buy", order_type="limit",
                quantity=0.001, price=100.0, reference_price=100.0,
                idempotency_key=f"position-cap-{symbol.replace('/', '')}",
            )
            assert result.ok
            db.commit()

        blocked = svc.create_shadow_order(
            db, env="demo", market="spot", symbol="SOL/USDT", side="buy", order_type="limit",
            quantity=0.001, price=100.0, reference_price=100.0,
            idempotency_key="position-cap-SOLUSDT",
        )
        assert not blocked.ok
        assert blocked.message == "max_open_positions_reached"
        assert blocked.details == {"open_positions": 2, "max_open_positions": 2}
        assert len(svc.get_portfolio(db, env="demo", market="spot")["positions"]) == 2
    finally:
        db.close()


def test_shadow_add_to_existing_symbol_allowed_at_position_cap(tmp_path):
    db = _db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="spot",
            bot_run_state="running", autonomous_enabled=False, emergency_stop=False,
            risk_limits={"demo_initial_balance": 150.0, "max_open_positions_cap": 1},
        ))
        db.commit()
        svc = ShadowTradingService()
        first = svc.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
            quantity=0.001, price=100.0, reference_price=100.0, idempotency_key="position-cap-BTC-1",
        )
        db.commit()
        assert first.ok

        add = svc.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
            quantity=0.001, price=100.0, reference_price=100.0, idempotency_key="position-cap-BTC-2",
        )
        db.commit()
        assert add.ok
        assert add.position.quantity == 0.002
        assert svc.get_portfolio(db, env="demo", market="spot")["snapshot"]["positions_open"] == 1
    finally:
        db.close()


def test_shadow_protection_never_uses_stale_cached_mark_price(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        buy = svc.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.1, price=100, reference_price=100,
            idempotency_key="protection-stale-mark-buy",
        )
        db.commit()
        assert svc.set_position_protection(db, position_id=buy.position.id, stop_loss=95.0)["ok"] is True
        position = db.get(PositionRecord, buy.position.id)
        position.mark_price = 90.0
        db.add(position)
        db.commit()

        exits = svc.evaluate_position_protection(db, env="demo", market="spot", prices={})
        db.commit()
        assert exits == []
        assert db.get(PositionRecord, buy.position.id).status == "open"
    finally:
        db.close()


def test_shadow_mark_updates_and_protection_reject_non_finite_values(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        buy = svc.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.1, price=100, reference_price=100,
            idempotency_key="protection-nan-mark-buy",
        )
        db.commit()
        assert svc.update_mark_prices(db, env="demo", market="spot", prices={"BTC/USDT": float("nan")}) == 0
        position = db.get(PositionRecord, buy.position.id)
        assert position.mark_price == 100.0
        assert svc.set_position_protection(
            db, position_id=buy.position.id, stop_loss=float("nan"),
        )["ok"] is False
    finally:
        db.close()


def test_emergency_stop_cannot_be_cleared_through_generic_state_update(tmp_path):
    from app.services.core_service import core_service

    db = _db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="spot",
            bot_run_state="stopped", autonomous_enabled=False, emergency_stop=True,
        ))
        db.commit()
        try:
            core_service.set_state(db, emergency_stop=False)
        except ValueError as exc:
            assert str(exc) == "emergency_stop_reset_requires_verified_reset_flow"
        else:
            raise AssertionError("generic state update bypassed emergency reset checks")
        assert db.get(SystemState, 1).emergency_stop is True
    finally:
        db.close()


def test_control_flags_reject_string_booleans(tmp_path):
    from app.services.core_service import core_service

    db = _db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="spot",
            bot_run_state="stopped", autonomous_enabled=False, emergency_stop=False,
        ))
        db.commit()
        try:
            core_service.set_state(db, autonomous_enabled="false")
        except ValueError as exc:
            assert str(exc) == "autonomous_enabled_must_be_boolean"
        else:
            raise AssertionError("string boolean was accepted for autonomous_enabled")
        assert db.get(SystemState, 1).autonomous_enabled is False
    finally:
        db.close()


def test_trailing_stop_fails_closed_on_non_finite_persisted_state(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        buy = svc.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.1, price=100, reference_price=100,
            idempotency_key="protection-nan-trail-state",
        )
        db.commit()
        assert svc.set_position_protection(
            db, position_id=buy.position.id, trailing_stop_pct=0.05,
        )["ok"] is True
        position = db.get(PositionRecord, buy.position.id)
        position.metadata_json = {**position.metadata_json, "highest_price": float("nan")}
        db.add(position); db.commit()

        exits = svc.evaluate_position_protection(
            db, env="demo", market="spot", prices={"BTC/USDT": 110.0},
        )
        assert len(exits) == 1
        assert exits[0]["ok"] is False
        assert exits[0]["error"] == "non_finite_trailing_stop_state"
        assert db.get(PositionRecord, buy.position.id).status == "open"
    finally:
        db.close()


def test_trailing_stop_configuration_ignores_non_finite_cached_mark(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        buy = svc.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.1, price=100, reference_price=100,
            idempotency_key="protection-nan-initial-mark",
        )
        db.commit()
        position = db.get(PositionRecord, buy.position.id)
        position.mark_price = float("nan")
        db.add(position); db.commit()

        result = svc.set_position_protection(
            db, position_id=buy.position.id, trailing_stop_pct=0.05,
        )
        assert result["ok"] is True
        assert result["protection"]["trailing_stop_pct"] == 0.05
        assert db.get(PositionRecord, buy.position.id).metadata_json["highest_price"] == 100.0
    finally:
        db.close()


def test_futures_shadow_short_uses_mark_pnl_margin_and_reduce_only_close(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        opened = svc.create_shadow_order(
            db, env="demo", market="futures", symbol="BTC/USDT", side="sell",
            order_type="limit", quantity=0.01, price=1000.0, reference_price=1000.0,
            idempotency_key="futures-short-open-001", leverage=5.0,
        )
        assert opened.ok is True
        assert opened.position.side == "short"
        assert opened.position.metadata_json["leverage"] == 5.0
        db.commit()

        updated = svc.update_mark_prices(db, env="demo", market="futures", prices={"BTC/USDT": 900.0})
        db.commit()
        assert updated == 1
        position = db.get(PositionRecord, opened.position.id)
        assert position.unrealized_pnl == 1.0
        portfolio = svc.get_portfolio(db, env="demo", market="futures")
        assert portfolio["snapshot"]["cash_balance"] < 150.0
        assert portfolio["snapshot"]["equity_estimate"] > 150.0

        closed = svc.close_shadow_position(db, position_id=position.id, close_price=900.0)
        db.commit()
        assert closed["ok"] is True
        final = db.get(PositionRecord, position.id)
        assert final.status == "closed"
        assert final.quantity == 0.0
        assert final.realized_pnl == 1.0
        close_order = db.get(OrderRecord, closed["order_id"])
        assert close_order.metadata_json["reduce_only"] is True
        assert close_order.side == "buy"
    finally:
        db.close()


def test_futures_shadow_caps_aggregate_position_notional(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        first = svc.create_shadow_order(
            db, env="demo", market="futures", symbol="ETH/USDT", side="buy",
            order_type="limit", quantity=0.01, price=1000.0, reference_price=1000.0,
            idempotency_key="futures-cap-open-001", leverage=10.0,
        )
        assert first.ok is True
        db.commit()
        rejected = svc.create_shadow_order(
            db, env="demo", market="futures", symbol="ETH/USDT", side="buy",
            order_type="limit", quantity=0.30, price=1000.0, reference_price=1000.0,
            idempotency_key="futures-cap-add-002", leverage=10.0,
        )
        assert rejected.ok is False
        assert rejected.message == "futures_position_notional_exceeds_limit"
    finally:
        db.close()


def test_emergency_stop_allows_reduce_only_futures_close(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        opened = svc.create_shadow_order(
            db, env="demo", market="futures", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.01, price=1000.0, reference_price=1000.0,
            idempotency_key="futures-emergency-open-001", leverage=5.0,
        )
        assert opened.ok is True
        state = SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="futures",
            bot_run_state="stopped", autonomous_enabled=False, emergency_stop=True,
        )
        db.add(state); db.commit()
        closed = svc.close_shadow_position(db, position_id=opened.position.id, close_price=1000.0)
        db.commit()
        assert closed["ok"] is True
        assert db.get(PositionRecord, opened.position.id).status == "closed"
    finally:
        db.close()


def test_futures_short_trailing_stop_tracks_lower_price_and_closes_on_reversal(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        opened = svc.create_shadow_order(
            db, env="demo", market="futures", symbol="ETH/USDT", side="sell",
            order_type="limit", quantity=0.01, price=1000.0, reference_price=1000.0,
            idempotency_key="futures-trail-short-001", leverage=5.0,
        )
        assert opened.ok is True
        db.commit()
        protected = svc.set_position_protection(
            db, position_id=opened.position.id, trailing_stop_pct=0.05,
        )
        assert protected["ok"] is True
        assert protected["protection"]["lowest_price"] == 1000.0

        first = svc.evaluate_position_protection(
            db, env="demo", market="futures", prices={"ETH/USDT": 900.0},
        )
        assert first == []
        db.commit()
        second = svc.evaluate_position_protection(
            db, env="demo", market="futures", prices={"ETH/USDT": 950.0},
        )
        db.commit()
        assert len(second) == 1
        assert second[0]["reason"] == "trailing_stop"
        assert second[0]["ok"] is True
        assert db.get(PositionRecord, opened.position.id).status == "closed"
    finally:
        db.close()


def test_close_shadow_position_rejects_non_shadow_and_invalid_close_values(tmp_path):
    db = _db(tmp_path)
    try:
        live_position = PositionRecord(
            environment_mode="live", market_type="spot", symbol="BTC/USDT",
            side="long_spot", quantity=0.1, entry_avg_price=100,
            mark_price=100, status="open", shadow=False,
        )
        db.add(live_position)
        db.commit()
        service = ShadowTradingService()
        blocked = service.close_shadow_position(db, position_id=live_position.id, close_price=110)
        assert blocked == {"ok": False, "reason": "open_shadow_position_required"}

        shadow = service.create_shadow_order(
            db, env="demo", market="spot", symbol="ETH/USDT", side="buy",
            order_type="limit", quantity=0.1, price=100, reference_price=100,
            idempotency_key="close-validation-shadow-01",
        )
        db.commit()
        bad_price = service.close_shadow_position(db, position_id=shadow.position.id, close_price=float("nan"))
        assert bad_price == {"ok": False, "reason": "non_finite_close_numeric_input"}
        boolean_price = service.close_shadow_position(db, position_id=shadow.position.id, close_price=True)
        assert boolean_price == {"ok": False, "reason": "boolean_close_numeric_input_not_allowed"}
        assert service.close_shadow_position(db, position_id=True, close_price=100)["reason"] == "invalid_position_id"
        assert service.set_position_protection(db, position_id=True, stop_loss=90)["error"] == "invalid_position_id"
    finally:
        db.close()


def test_portfolio_reads_do_not_create_duplicate_valuation_snapshots(tmp_path):
    db = _db(tmp_path)
    try:
        service = ShadowTradingService()
        buy = service.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.001, price=100, reference_price=100,
            idempotency_key="portfolio-read-no-write-001",
        )
        assert buy.ok
        db.commit()
        before = db.query(PortfolioSnapshot).count()
        first = service.get_portfolio(db, env="demo", market="spot")
        second = service.get_portfolio(db, env="demo", market="spot")
        after = db.query(PortfolioSnapshot).count()
        assert before == 1
        assert after == before
        assert first["snapshot"]["equity_estimate"] == second["snapshot"]["equity_estimate"]
        assert first["snapshot"]["id"] is None
    finally:
        db.close()


def test_futures_short_intent_maps_to_sell_and_closes_with_reduce_only_buy(tmp_path):
    db = _db(tmp_path)
    try:
        service = ShadowTradingService()
        opened = service.create_shadow_order(
            db, env="demo", market="futures", symbol="ETH/USDT", side="short",
            order_type="market", quantity=0.01, price=100, reference_price=100,
            idempotency_key="futures-short-intent-001", leverage=2,
        )
        assert opened.ok
        assert opened.order.side == "sell"
        assert opened.position.side == "short"
        closed = service.close_shadow_position(db, position_id=opened.position.id, close_price=90)
        assert closed["ok"] is True
        exit_order = db.get(OrderRecord, closed["order_id"])
        assert exit_order.side == "buy"
        assert exit_order.metadata_json["reduce_only"] is True
        assert opened.position.status == "closed"
    finally:
        db.close()


def test_position_protection_rejects_boolean_values(tmp_path):
    db = _db(tmp_path)
    try:
        service = ShadowTradingService()
        opened = service.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.001, price=100, reference_price=100,
            idempotency_key="protection-bool-input-01",
        )
        assert opened.ok
        for field in ("stop_loss", "take_profit", "trailing_stop_pct"):
            result = service.set_position_protection(
                db, position_id=opened.position.id, **{field: True},
            )
            assert result["ok"] is False
            assert result["error"] == "boolean_protection_value_not_allowed"
    finally:
        db.close()


def test_invalid_daily_loss_cap_fails_closed_for_new_shadow_entries(tmp_path):
    db = _db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="spot",
            bot_run_state="running", autonomous_enabled=False, emergency_stop=False,
            risk_limits={"demo_initial_balance": 150.0, "max_daily_loss_cap": float("nan")},
        ))
        db.commit()
        result = ShadowTradingService().create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.001, price=100, reference_price=100,
            idempotency_key="invalid-daily-cap-block-01",
        )
        assert result.ok is False
        assert result.message == "daily_loss_limit_reached"
    finally:
        db.close()


def test_position_protection_does_not_evaluate_boolean_mark_prices(tmp_path):
    db = _db(tmp_path)
    try:
        service = ShadowTradingService()
        opened = service.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.001, price=100, reference_price=100,
            idempotency_key="protection-bool-mark-001",
        )
        assert opened.ok
        service.set_position_protection(db, position_id=opened.position.id, stop_loss=90)
        exits = service.evaluate_position_protection(
            db, env="demo", market="spot", prices={"BTC/USDT": True},
        )
        assert exits[0]["error"] == "boolean_protection_value_not_allowed"
        assert opened.position.status == "open"
    finally:
        db.close()


def test_futures_shadow_enforces_aggregate_portfolio_exposure_cap(tmp_path):
    db = _db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="futures",
            risk_limits={"demo_initial_balance": 150.0},
        ))
        db.commit()
        svc = ShadowTradingService()
        first = svc.create_shadow_order(
            db, env="demo", market="futures", symbol="BTC/USDT", side="buy",
            order_type="market", quantity=0.01, price=10000, reference_price=10000,
            idempotency_key="portfolio-cap-btc", leverage=10,
        )
        db.commit()
        assert first.ok

        second = svc.create_shadow_order(
            db, env="demo", market="futures", symbol="ETH/USDT", side="buy",
            order_type="market", quantity=0.02, price=10000, reference_price=10000,
            idempotency_key="portfolio-cap-eth", leverage=10,
        )
        db.commit()
        assert second.ok

        # Existing exposure is 300 USDT (2x the 150 USDT initial equity).
        third = svc.create_shadow_order(
            db, env="demo", market="futures", symbol="SOL/USDT", side="buy",
            order_type="market", quantity=0.01, price=10000, reference_price=10000,
            idempotency_key="portfolio-cap-sol", leverage=10,
        )
        assert not third.ok
        assert third.message == "max_portfolio_exposure_exceeded"
        assert third.details["current_exposure"] == 300.0
        assert third.details["max_portfolio_exposure"] == 300.0
        assert third.details["projected_exposure"] == 400.0

        # A portfolio cap must never block risk-reducing exits.
        closed = svc.close_shadow_position(
            db, position_id=first.position.id, close_price=10000.0,
        )
        assert closed["ok"] is True
        assert db.get(PositionRecord, first.position.id).status == "closed"
    finally:
        db.close()


def test_spot_full_position_close_can_exceed_single_entry_cap(tmp_path):
    db = _db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="spot",
            risk_limits={"demo_initial_balance": 500.0},
        ))
        db.commit()
        service = ShadowTradingService()
        first = service.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="market", quantity=0.0016, price=50000.0,
            reference_price=50000.0, idempotency_key="spot-multi-buy-001",
        )
        assert first.ok
        db.commit()
        second = service.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy",
            order_type="market", quantity=0.0016, price=50000.0,
            reference_price=50000.0, idempotency_key="spot-multi-buy-002",
        )
        assert second.ok
        db.commit()
        assert second.position.quantity == 0.0032

        closed = service.close_shadow_position(
            db, position_id=second.position.id, close_price=50000.0,
        )
        assert closed["ok"] is True
        assert db.get(PositionRecord, second.position.id).status == "closed"
        exit_order = db.get(OrderRecord, closed["order_id"])
        assert exit_order.side == "sell"
        assert exit_order.quantity * exit_order.avg_fill_price == 160.0
    finally:
        db.close()


def test_futures_reduce_only_close_survives_mark_appreciation_above_entry_cap(tmp_path):
    db = _db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="futures",
            risk_limits={"demo_initial_balance": 1000.0},
        ))
        db.commit()
        service = ShadowTradingService()
        opened = service.create_shadow_order(
            db, env="demo", market="futures", symbol="BTC/USDT", side="buy",
            order_type="market", quantity=0.006, price=50000.0,
            reference_price=50000.0, idempotency_key="futures-close-appreciation-open",
            leverage=10,
        )
        assert opened.ok
        db.commit()
        assert service.update_mark_prices(
            db, env="demo", market="futures", prices={"BTC/USDT": 90000.0},
        ) == 1
        db.flush()

        closed = service.close_shadow_position(
            db, position_id=opened.position.id, close_price=90000.0,
        )
        assert closed["ok"] is True
        assert db.get(PositionRecord, opened.position.id).status == "closed"
        exit_order = db.get(OrderRecord, closed["order_id"])
        assert exit_order.metadata_json["reduce_only"] is True
        assert exit_order.quantity * exit_order.avg_fill_price == 540.0
    finally:
        db.close()


def test_futures_per_position_cap_uses_current_mark_not_entry_price(tmp_path):
    db = _db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="futures",
            risk_limits={"demo_initial_balance": 1000.0},
        ))
        db.commit()
        svc = ShadowTradingService()
        opened = svc.create_shadow_order(
            db, env="demo", market="futures", symbol="BTC/USDT", side="buy",
            order_type="market", quantity=0.01, price=1000.0, reference_price=1000.0,
            idempotency_key="mark-cap-open", leverage=10,
        )
        db.commit()
        assert opened.ok
        assert svc.update_mark_prices(db, env="demo", market="futures", prices={"BTC/USDT": 90000.0}) == 1
        db.flush()

        rejected = svc.create_shadow_order(
            db, env="demo", market="futures", symbol="BTC/USDT", side="buy",
            order_type="market", quantity=0.001, price=1000.0, reference_price=1000.0,
            idempotency_key="mark-cap-add", leverage=10,
        )
        assert not rejected.ok
        assert rejected.message == "futures_position_notional_exceeds_limit"
        assert rejected.details["position_notional"] > 800.0
    finally:
        db.close()


def test_futures_partial_reduce_only_recalculates_isolated_margin(tmp_path):
    from app.services.shadow_trading import ShadowTradingService

    db = _db(tmp_path)
    try:
        service = ShadowTradingService()
        opened = service.create_shadow_order(
            db, env="demo", market="futures", symbol="BTC/USDT", side="buy",
            order_type="market", quantity=0.2, price=100.0, reference_price=100.0,
            idempotency_key="futures-partial-margin-open", leverage=2.0,
        )
        db.commit()
        assert opened.ok
        assert opened.position.metadata_json["margin_mode"] == "isolated"
        assert opened.position.metadata_json["initial_margin_estimate"] == 10.0

        reduced = service.create_shadow_order(
            db, env="demo", market="futures", symbol="BTC/USDT", side="sell",
            order_type="market", quantity=0.1, price=110.0, reference_price=110.0,
            idempotency_key="futures-partial-margin-reduce", reduce_only=True,
        )
        db.commit()
        assert reduced.ok
        assert reduced.position.quantity == 0.1
        assert reduced.position.metadata_json["initial_margin_estimate"] == 5.0
        assert reduced.position.metadata_json["margin_mode"] == "isolated"
    finally:
        db.close()


def test_shadow_daily_loss_default_uses_global_seven_percent_cap(tmp_path, monkeypatch):
    db = _db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="spot",
            risk_limits={"demo_initial_balance": 150.0},
        ))
        db.commit()
        service = ShadowTradingService()
        monkeypatch.setattr(service, "_daily_realized_pnl", lambda *args, **kwargs: -11.0)
        monkeypatch.setattr(service, "_open_unrealized_pnl", lambda *args, **kwargs: 0.0)
        reached, daily_pnl, limit_amount = service._daily_loss_limit_reached(db, env="demo", market="spot")
        assert reached is True
        assert daily_pnl == -11.0
        assert abs(limit_amount - 10.5) < 1e-9
    finally:
        db.close()


def test_shadow_daily_loss_user_setting_cannot_exceed_global_cap(tmp_path, monkeypatch):
    db = _db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="spot",
            risk_limits={"demo_initial_balance": 150.0, "max_daily_loss_cap": 0.10},
        ))
        db.commit()
        service = ShadowTradingService()
        monkeypatch.setattr(service, "_daily_realized_pnl", lambda *args, **kwargs: -11.0)
        monkeypatch.setattr(service, "_open_unrealized_pnl", lambda *args, **kwargs: 0.0)
        reached, daily_pnl, limit_amount = service._daily_loss_limit_reached(db, env="demo", market="spot")
        assert reached is True
        assert daily_pnl == -11.0
        assert abs(limit_amount - 10.5) < 1e-9
    finally:
        db.close()
