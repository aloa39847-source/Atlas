from __future__ import annotations

import math
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import MarketType
from app.core.config import settings
from app.engines.order_gateway import PreTradeOrderGateway
from app.engines.risk import RiskEngine
from app.models.entities import OrderRecord, PortfolioSnapshot, PositionRecord, SystemState
from .audit import log_event


@dataclass
class ShadowFillResult:
    ok: bool
    order: OrderRecord | None
    position: PositionRecord | None
    message: str
    details: dict[str, Any]


class ShadowTradingService:
    """Shadow/demo order lifecycle. No real exchange orders.

    Lifecycle: validate (compliance+gateway+risk) -> create shadow order -> simulate fill/cancel/partial
    -> update position/portfolio -> audit/log. Idempotency keys prevent duplicate orders.
    """

    def __init__(self) -> None:
        self.gateway = PreTradeOrderGateway()
        self.risk = RiskEngine()

    def _get_or_create_position(self, db: Session, *, env: str, market: str, symbol: str, side: str, shadow: bool) -> PositionRecord:
        q = select(PositionRecord).where(
            PositionRecord.environment_mode == env, PositionRecord.market_type == market,
            PositionRecord.symbol == symbol, PositionRecord.side == side, PositionRecord.status == "open", PositionRecord.shadow == shadow,
        )
        pos = db.execute(q).scalar_one_or_none()
        if not pos:
            pos = PositionRecord(environment_mode=env, market_type=market, symbol=symbol, side=side, quantity=0.0, status="open", shadow=shadow, leverage=1.0)
            db.add(pos); db.flush()
        return pos

    def _orders(self, db: Session, *, env: str, market: str) -> list[OrderRecord]:
        return db.execute(select(OrderRecord).where(
            OrderRecord.environment_mode == env, OrderRecord.market_type == market, OrderRecord.shadow.is_(True)
        )).scalars().all()

    def _initial_cash(self, db: Session, *, env: str) -> float:
        if env != "demo":
            return 150.0
        state = db.get(SystemState, 1)
        raw_limits = getattr(state, "risk_limits", None) if state else None
        if raw_limits is not None and not isinstance(raw_limits, dict):
            return 0.0
        limits = raw_limits or {}
        configured = limits.get("demo_initial_balance", 150.0)
        if isinstance(configured, bool):
            return 0.0
        try:
            value = float(configured)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        return value if math.isfinite(value) and value > 0 else 0.0

    def _daily_realized_pnl(self, db: Session, *, env: str, market: str) -> float:
        today = datetime.now(timezone.utc).date()
        total = 0.0
        for order in self._orders(db, env=env, market=market):
            created = order.created_at
            if created is None or created.date() != today:
                continue
            metadata = order.metadata_json if isinstance(order.metadata_json, dict) else {}
            if metadata.get("realized_pnl_net") is None:
                continue
            try:
                realized_net = float(metadata["realized_pnl_net"])
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(realized_net):
                total += realized_net
        for position in db.execute(select(PositionRecord).where(
            PositionRecord.environment_mode == env,
            PositionRecord.market_type == market,
            PositionRecord.shadow.is_(True),
        )).scalars().all():
            metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
            if metadata.get("funding_daily_date") != today.isoformat():
                continue
            raw_funding_pnl = metadata.get("funding_daily_pnl", 0.0)
            if isinstance(raw_funding_pnl, bool):
                continue
            try:
                funding_pnl = float(raw_funding_pnl)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(funding_pnl):
                total += funding_pnl
        return round(total, 8)

    def _open_unrealized_pnl(self, db: Session, *, env: str, market: str) -> float:
        positions = db.execute(select(PositionRecord).where(
            PositionRecord.environment_mode == env, PositionRecord.market_type == market,
            PositionRecord.status == "open", PositionRecord.shadow.is_(True),
        )).scalars().all()
        total = 0.0
        for position in positions:
            metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
            try:
                unrealized = float(position.unrealized_pnl or 0.0)
                remaining_entry_fees = float(metadata.get("entry_fees_remaining", 0.0))
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(unrealized) and math.isfinite(remaining_entry_fees) and remaining_entry_fees >= 0:
                total += unrealized - remaining_entry_fees
        return round(total, 8)

    def update_mark_prices(self, db: Session, *, env: str, market: str, prices: dict[str, float]) -> int:
        """Refresh open Shadow position marks before evaluating the daily-loss guard."""
        positions = db.execute(select(PositionRecord).where(
            PositionRecord.environment_mode == env, PositionRecord.market_type == market,
            PositionRecord.status == "open", PositionRecord.shadow.is_(True),
        )).scalars().all()
        updated = 0
        for position in positions:
            raw_price = prices.get(position.symbol)
            if raw_price is None:
                continue
            if isinstance(raw_price, bool):
                continue
            try:
                price = float(raw_price)
                entry = float(position.entry_avg_price or 0.0)
                quantity = float(position.quantity or 0.0)
            except (TypeError, ValueError, OverflowError):
                continue
            if not all(math.isfinite(value) for value in (price, entry, quantity)) or price <= 0 or entry <= 0 or quantity <= 0:
                continue
            direction = -1.0 if position.side in {"short", "short_futures", "sell"} else 1.0
            position.mark_price = price
            position.unrealized_pnl = round(direction * (price - entry) * quantity, 8)
            db.add(position)
            updated += 1
        return updated

    def set_position_protection(
        self, db: Session, *, position_id: int, stop_loss: float | None = None,
        take_profit: float | None = None, trailing_stop_pct: float | None = None,
    ) -> dict[str, Any]:
        if isinstance(position_id, bool) or not isinstance(position_id, int) or position_id <= 0:
            return {"ok": False, "error": "invalid_position_id"}
        position = db.get(PositionRecord, position_id)
        if not position or position.status != "open" or not position.shadow:
            return {"ok": False, "error": "open_shadow_position_required"}
        is_short = position.side in {"short", "short_futures", "sell"}
        is_spot_long = position.market_type == MarketType.SPOT.value and position.side == "long_spot"
        is_futures_position = position.market_type == MarketType.FUTURES.value and position.side in {"long", "short", "buy", "sell"}
        if not (is_spot_long or is_futures_position):
            return {"ok": False, "error": "supported_shadow_spot_or_futures_position_required"}
        entry = float(position.entry_avg_price or 0.0)
        if not math.isfinite(entry) or entry <= 0:
            return {"ok": False, "error": "invalid_position_entry"}
        if any(isinstance(value, bool) for value in (stop_loss, take_profit, trailing_stop_pct) if value is not None):
            return {"ok": False, "error": "boolean_protection_value_not_allowed"}
        try:
            if stop_loss is not None:
                stop_loss = float(stop_loss)
            if take_profit is not None:
                take_profit = float(take_profit)
            if trailing_stop_pct is not None:
                trailing_stop_pct = float(trailing_stop_pct)
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "invalid_protection_numeric_input"}
        if stop_loss is not None:
            stop_loss = float(stop_loss)
            if (not math.isfinite(stop_loss) or stop_loss <= 0
                    or (is_short and stop_loss <= entry)
                    or (not is_short and stop_loss >= entry)):
                return {"ok": False, "error": "stop_loss_on_loss_side_of_entry_required"}
        if take_profit is not None:
            if (not math.isfinite(take_profit) or take_profit <= 0
                    or (is_short and take_profit >= entry)
                    or (not is_short and take_profit <= entry)):
                return {"ok": False, "error": "take_profit_on_profit_side_of_entry_required"}
        if trailing_stop_pct is not None:
            if not math.isfinite(trailing_stop_pct) or not (0 < trailing_stop_pct <= 0.20):
                return {"ok": False, "error": "trailing_stop_pct_out_of_range"}
        if stop_loss is None and take_profit is None and trailing_stop_pct is None:
            return {"ok": False, "error": "at_least_one_protection_required"}
        raw_metadata = position.metadata_json
        if raw_metadata is not None and not isinstance(raw_metadata, dict):
            return {"ok": False, "error": "invalid_position_protection_metadata"}
        metadata = dict(raw_metadata or {})
        if stop_loss is not None:
            metadata["stop_loss"] = round(float(stop_loss), 12)
        if take_profit is not None:
            metadata["take_profit"] = round(float(take_profit), 12)
        if trailing_stop_pct is not None:
            try:
                current_mark = float(position.mark_price) if position.mark_price is not None else entry
            except (TypeError, ValueError, OverflowError):
                current_mark = entry
            if not math.isfinite(current_mark) or current_mark <= 0:
                current_mark = entry
            metadata["trailing_stop_pct"] = float(trailing_stop_pct)
            if is_short:
                metadata["lowest_price"] = min(current_mark, entry)
                metadata.pop("highest_price", None)
            else:
                metadata["highest_price"] = max(current_mark, entry)
                metadata.pop("lowest_price", None)
            metadata["trailing_stop_price"] = None
        metadata["protection_updated_at"] = datetime.now(timezone.utc).isoformat()
        position.metadata_json = metadata
        db.add(position)
        db.flush()
        return {"ok": True, "position_id": position.id, "protection": {
            "stop_loss": metadata.get("stop_loss"), "take_profit": metadata.get("take_profit"),
            "trailing_stop_pct": metadata.get("trailing_stop_pct"),
            "trailing_stop_price": metadata.get("trailing_stop_price"),
            "highest_price": metadata.get("highest_price"),
            "lowest_price": metadata.get("lowest_price"),
        }}

    def evaluate_position_protection(
        self, db: Session, *, env: str, market: str, prices: dict[str, float],
    ) -> list[dict[str, Any]]:
        self.update_mark_prices(db, env=env, market=market, prices=prices)
        positions = db.execute(select(PositionRecord).where(
            PositionRecord.environment_mode == env, PositionRecord.market_type == market,
            PositionRecord.status == "open", PositionRecord.shadow.is_(True),
        )).scalars().all()
        exits: list[dict[str, Any]] = []
        for position in positions:
            raw_metadata = position.metadata_json
            if raw_metadata is not None and not isinstance(raw_metadata, dict):
                exits.append({"ok": False, "position_id": position.id, "error": "invalid_protection_metadata"})
                continue
            metadata = dict(raw_metadata or {})
            # Never evaluate protection from a cached mark when this cycle did
            # not provide a fresh price for the symbol.
            if position.symbol not in prices:
                continue
            if any(isinstance(metadata.get(key), bool) for key in (
                "stop_loss", "take_profit", "trailing_stop_pct",
                "trailing_stop_price", "highest_price", "lowest_price",
            )) or isinstance(prices[position.symbol], bool):
                exits.append({"ok": False, "position_id": position.id, "error": "boolean_protection_value_not_allowed"})
                continue
            try:
                price = float(prices[position.symbol])
                stop_loss = float(metadata["stop_loss"]) if metadata.get("stop_loss") is not None else None
                take_profit = float(metadata["take_profit"]) if metadata.get("take_profit") is not None else None
                trailing_pct = float(metadata["trailing_stop_pct"]) if metadata.get("trailing_stop_pct") is not None else None
            except (TypeError, ValueError, OverflowError):
                exits.append({"ok": False, "position_id": position.id, "error": "invalid_protection_values"})
                continue
            numeric_values = [price] + [value for value in (stop_loss, take_profit, trailing_pct) if value is not None]
            if not all(math.isfinite(value) for value in numeric_values) or price <= 0:
                exits.append({"ok": False, "position_id": position.id, "error": "non_finite_or_invalid_protection_values"})
                continue
            if not any(value is not None for value in (stop_loss, take_profit, trailing_pct)):
                continue
            is_short = position.side in {"short", "short_futures", "sell"}
            entry = float(position.entry_avg_price or 0.0)
            if ((stop_loss is not None and ((is_short and stop_loss <= entry) or (not is_short and stop_loss >= entry)))
                    or (take_profit is not None and ((is_short and take_profit >= entry) or (not is_short and take_profit <= entry)))
                    or (trailing_pct is not None and not 0 < trailing_pct <= 0.20)):
                exits.append({"ok": False, "position_id": position.id, "error": "protection_direction_or_range_invalid"})
                continue
            reason = None
            if stop_loss is not None and ((is_short and price >= stop_loss) or (not is_short and price <= stop_loss)):
                reason = "stop_loss"
            elif take_profit is not None and ((is_short and price <= take_profit) or (not is_short and price >= take_profit)):
                reason = "take_profit"
            elif trailing_pct is not None:
                try:
                    previous_raw = metadata.get("trailing_stop_price")
                    previous_value = float(previous_raw) if previous_raw is not None else None
                    if is_short:
                        lowest_raw = metadata.get("lowest_price")
                        extreme_value = float(lowest_raw) if lowest_raw is not None else entry
                    else:
                        highest_raw = metadata.get("highest_price")
                        extreme_value = float(highest_raw) if highest_raw is not None else entry
                except (TypeError, ValueError, OverflowError):
                    exits.append({"ok": False, "position_id": position.id, "error": "invalid_trailing_stop_state"})
                    continue
                if (not math.isfinite(extreme_value) or extreme_value <= 0
                        or (previous_value is not None and (not math.isfinite(previous_value) or previous_value <= 0))):
                    exits.append({"ok": False, "position_id": position.id, "error": "non_finite_trailing_stop_state"})
                    continue
                if is_short:
                    extreme = min(extreme_value, price)
                    trail = extreme * (1.0 + trailing_pct)
                    if previous_value is not None:
                        trail = min(trail, previous_value)
                    metadata["lowest_price"] = extreme
                    triggered = price >= trail
                else:
                    extreme = max(extreme_value, price)
                    trail = extreme * (1.0 - trailing_pct)
                    if previous_value is not None:
                        trail = max(trail, previous_value)
                    metadata["highest_price"] = extreme
                    triggered = price <= trail
                if not math.isfinite(trail) or trail <= 0:
                    exits.append({"ok": False, "position_id": position.id, "error": "invalid_computed_trailing_stop"})
                    continue
                metadata["trailing_stop_price"] = round(trail, 12)
                position.metadata_json = metadata
                db.add(position)
                if triggered:
                    reason = "trailing_stop"
            if reason:
                closed = self.close_shadow_position(db, position_id=position.id, close_price=price)
                exits.append({"position_id": position.id, "reason": reason, "price": price, **closed})
        return exits

    def _daily_loss_limit_reached(self, db: Session, *, env: str, market: str) -> tuple[bool, float, float]:
        state = db.get(SystemState, 1)
        raw_limits = getattr(state, "risk_limits", None) if state else None
        try:
            global_cap = float(settings.max_daily_loss_cap)
        except (TypeError, ValueError, OverflowError):
            global_cap = float("nan")
        safe_global_cap = global_cap if math.isfinite(global_cap) and 0 < global_cap <= 1 else 0.0
        fallback_limit = self._initial_cash(db, env=env) * safe_global_cap
        if raw_limits is not None and not isinstance(raw_limits, dict):
            return True, -fallback_limit, fallback_limit
        limits = raw_limits or {}
        try:
            raw_cap = limits.get("max_daily_loss_cap", safe_global_cap)
            if isinstance(raw_cap, bool):
                raise ValueError("boolean_daily_loss_cap")
            cap = float(raw_cap)
        except (TypeError, ValueError, OverflowError):
            cap = float("nan")
        if (not math.isfinite(cap) or cap <= 0 or cap > 1
                or safe_global_cap <= 0):
            # Invalid configuration or invalid global policy blocks new entries.
            return True, -fallback_limit, fallback_limit
        # Per-account settings may tighten the cap, never loosen the project-wide ceiling.
        cap = min(cap, safe_global_cap)
        limit_amount = self._initial_cash(db, env=env) * cap
        # Include today's realized PnL and the latest mark-to-market PnL of
        # open positions. Carried positions are treated conservatively.
        daily_realized = self._daily_realized_pnl(db, env=env, market=market)
        open_unrealized = self._open_unrealized_pnl(db, env=env, market=market)
        daily_pnl = round(daily_realized + open_unrealized, 8)
        return daily_pnl <= -limit_amount, daily_pnl, limit_amount

    def _cash_balance(self, db: Session, *, env: str, market: str, initial_cash: float | None = None) -> float:
        initial_cash = self._initial_cash(db, env=env) if initial_cash is None else float(initial_cash)
        if market == "futures":
            all_positions = db.execute(select(PositionRecord).where(
                PositionRecord.environment_mode == env, PositionRecord.market_type == market,
                PositionRecord.shadow.is_(True),
            )).scalars().all()
            open_positions = [position for position in all_positions if position.status == "open" and position.quantity > 0]
            orders = self._orders(db, env=env, market=market)
            realized = sum(float(position.realized_pnl or 0.0) for position in all_positions)
            unrealized = sum(float(position.unrealized_pnl or 0.0) for position in open_positions)
            fees = sum(float(order.fees_paid or 0.0) for order in orders)
            margin_used = 0.0
            for position in open_positions:
                metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
                raw_leverage = metadata.get("leverage", 1.0)
                if isinstance(raw_leverage, bool):
                    return 0.0
                try:
                    leverage = float(raw_leverage)
                    notional = float(position.entry_avg_price or 0.0) * float(position.quantity or 0.0)
                except (TypeError, ValueError, OverflowError):
                    return 0.0
                if not math.isfinite(leverage) or leverage < 1.0 or not math.isfinite(notional) or notional < 0:
                    return 0.0
                margin_used += notional / leverage
            equity = initial_cash + realized + unrealized - fees
            available = equity - margin_used
            if not all(math.isfinite(value) for value in (equity, margin_used, available)):
                return 0.0
            return round(max(0.0, available), 8)
        if market != "spot":
            return float(initial_cash)
        cash = float(initial_cash)
        for order in self._orders(db, env=env, market=market):
            qty = float(order.filled_quantity or 0.0)
            px = float(order.avg_fill_price or order.price or 0.0)
            notional = qty * px
            if order.side.lower() == "buy":
                cash -= notional
            elif order.side.lower() == "sell":
                cash += notional
            cash -= float(order.fees_paid or 0.0)
        return round(cash, 8)

    def get_available_cash(self, db: Session, *, env: str, market: str) -> float:
        """Return currently available quote cash for Spot risk sizing."""
        return self._cash_balance(db, env=env, market=market)

    def _snapshot(self, db: Session, *, env: str, market: str, cash: float | None = None) -> PortfolioSnapshot:
        cash = self._initial_cash(db, env=env) if cash is None else float(cash)
        all_positions = db.execute(select(PositionRecord).where(
            PositionRecord.environment_mode == env, PositionRecord.market_type == market, PositionRecord.shadow.is_(True)
        )).scalars().all()
        positions = [p for p in all_positions if p.status == "open" and p.quantity > 0]
        orders = self._orders(db, env=env, market=market)
        exposure = sum(abs((p.mark_price or p.entry_avg_price or 0) * p.quantity) for p in positions)
        unreal = sum(p.unrealized_pnl for p in positions)
        realized = sum(p.realized_pnl for p in all_positions)
        fees = sum(float(o.fees_paid or 0.0) for o in orders)
        cash_balance = self._cash_balance(db, env=env, market=market, initial_cash=cash)
        if market == "spot":
            market_value = sum((p.mark_price or p.entry_avg_price or 0.0) * p.quantity for p in positions)
            equity = cash_balance + market_value
            accounting = "shadow_spot_cashflow"
        elif market == "futures":
            equity = cash + unreal + realized - fees
            accounting = "shadow_futures_margin_estimate"
        else:
            equity = cash_balance + unreal + realized
            accounting = "shadow_estimate"
        snap = PortfolioSnapshot(environment_mode=env, market_type=market, cash_balance=cash_balance, equity_estimate=equity,
                                 unrealized_pnl=unreal, realized_pnl=realized, fees_total=fees, exposure_notional=exposure,
                                 positions_open=len(positions), details={"order_count": len(orders), "accounting": accounting})
        db.add(snap); db.flush()
        return snap

    def create_shadow_order(self, db: Session, *, env: str, market: str, symbol: str, side: str, order_type: str,
                            quantity: float, price: float | None = None, reference_price: float | None = None,
                            idempotency_key: str | None = None, simulate: str = "fill", fill_ratio: float = 1.0,
                            fee_rate: float = 0.001, mark_price: float | None = None,
                            leverage: float = 1.0, reduce_only: bool = False,
                            verified_position_close: bool = False) -> ShadowFillResult:
        mkt = MarketType(market)
        if mkt == MarketType.EARN:
            return ShadowFillResult(False, None, None, "earn_not_order_execution", {"market": mkt.value, "orders_sent": False})
        if type(reduce_only) is not bool:
            return ShadowFillResult(False, None, None, "reduce_only_must_be_boolean", {"orders_sent": False})
        if type(verified_position_close) is not bool:
            return ShadowFillResult(False, None, None, "verified_position_close_must_be_boolean", {"orders_sent": False})
        if verified_position_close and (mkt != MarketType.SPOT or str(side or "").strip().lower() != "sell"):
            return ShadowFillResult(False, None, None, "verified_position_close_spot_sell_only", {"orders_sent": False})
        if isinstance(leverage, bool):
            return ShadowFillResult(False, None, None, "invalid_leverage", {"orders_sent": False})
        try:
            leverage = float(leverage)
            fee_rate = float(fee_rate)
            fill_ratio = float(fill_ratio)
            mark_price = float(mark_price) if mark_price is not None else None
        except (TypeError, ValueError, OverflowError):
            return ShadowFillResult(False, None, None, "invalid_shadow_execution_numeric_input", {"orders_sent": False})
        if not math.isfinite(fee_rate) or not 0.0 <= fee_rate <= 0.01:
            return ShadowFillResult(False, None, None, "fee_rate_out_of_range", {"orders_sent": False})
        if not math.isfinite(fill_ratio) or not 0.0 <= fill_ratio <= 1.0:
            return ShadowFillResult(False, None, None, "fill_ratio_out_of_range", {"orders_sent": False})
        if mark_price is not None and (not math.isfinite(mark_price) or mark_price <= 0):
            return ShadowFillResult(False, None, None, "invalid_mark_price", {"orders_sent": False})
        if not isinstance(simulate, str) or simulate not in {"fill", "partial", "connection_loss", "cancel", "reject"}:
            return ShadowFillResult(False, None, None, "unsupported_shadow_simulation_mode", {"orders_sent": False})
        if not math.isfinite(leverage) or leverage < 1.0:
            return ShadowFillResult(False, None, None, "invalid_leverage", {"orders_sent": False})
        if mkt == MarketType.SPOT and leverage != 1.0:
            return ShadowFillResult(False, None, None, "spot_leverage_not_supported", {"orders_sent": False})
        if mkt == MarketType.FUTURES and leverage > settings.max_leverage_cap:
            return ShadowFillResult(False, None, None, "leverage_exceeds_limit", {"max_leverage": settings.max_leverage_cap, "orders_sent": False})
        if mkt == MarketType.SPOT and reduce_only:
            return ShadowFillResult(False, None, None, "spot_reduce_only_not_supported", {"orders_sent": False})
        if not isinstance(idempotency_key, str):
            return ShadowFillResult(False, None, None, "idempotency_key_required", {"orders_sent": False})
        idem = idempotency_key.strip()
        if not 8 <= len(idem) <= 128:
            return ShadowFillResult(False, None, None, "idempotency_key_invalid_length", {"orders_sent": False})
        existing = db.execute(select(OrderRecord).where(OrderRecord.idempotency_key == idem)).scalar_one_or_none()
        if existing:
            return ShadowFillResult(True, existing, None, "duplicate_idempotency_returned_existing", {"duplicate": True, "order_id": existing.id})

        side_normalized = str(side or "").strip().lower()
        if mkt == MarketType.FUTURES:
            if reduce_only and side_normalized not in {"buy", "sell"}:
                return ShadowFillResult(False, None, None, "reduce_only_requires_buy_or_sell_side", {"orders_sent": False})
            if not reduce_only:
                side_normalized = {"long": "buy", "short": "sell"}.get(side_normalized, side_normalized)
        is_reducing = (mkt == MarketType.SPOT and side_normalized == "sell") or (
            mkt == MarketType.FUTURES and reduce_only
        )
        # Daily loss guard blocks new entries on both sides of Futures, but
        # reducing orders remain available to lower risk.
        if not is_reducing:
            hit_limit, daily_pnl, limit_amount = self._daily_loss_limit_reached(db, env=env, market=market)
            if hit_limit:
                if system_state := db.get(SystemState, 1):
                    system_state.bot_run_state = "stopped"
                    system_state.autonomous_enabled = False
                    system_state.bot_stop_reason = "daily_loss_limit_reached"
                    system_state.bot_stopped_at = datetime.now(timezone.utc)
                    db.add(system_state)
                log_event(db, action="shadow.daily_loss_limit", severity="warning",
                          message="New entries paused after configured daily realized plus open-position unrealized loss limit.",
                          environment_mode=env, market_type=market,
                          details={"daily_realized_plus_unrealized_pnl": daily_pnl, "loss_limit_amount": limit_amount})
                return ShadowFillResult(False, None, None, "daily_loss_limit_reached",
                                         {"daily_realized_plus_unrealized_pnl": daily_pnl, "loss_limit_amount": limit_amount})

            # Enforce the portfolio position-count limit again at the execution
            # boundary. Strategy/risk analysis is advisory; this guard cannot
            # be bypassed by a direct Shadow order request.
            state = db.get(SystemState, 1)
            raw_limits = getattr(state, "risk_limits", None) if state else None
            if raw_limits is not None and not isinstance(raw_limits, dict):
                configured_raw = None
            else:
                limits = raw_limits or {}
                configured_raw = limits.get("max_open_positions_cap", settings.max_open_positions_cap)
            # Risk-limit configuration is security-sensitive: reject booleans,
            # floats, strings, and malformed values by failing closed instead
            # of silently restoring the more permissive application default.
            if type(configured_raw) is not int:
                configured_max = 0
            else:
                configured_max = configured_raw
            max_positions = max(0, min(configured_max, settings.max_open_positions_cap))
            open_positions = db.execute(select(PositionRecord).where(
                PositionRecord.environment_mode == env,
                PositionRecord.market_type == market,
                PositionRecord.status == "open",
                PositionRecord.shadow.is_(True),
            )).scalars().all()
            target_position_side = (
                "long_spot" if mkt == MarketType.SPOT
                else ("long" if side_normalized == "buy" else "short")
            )
            already_holds_symbol = any(p.symbol == symbol and p.side == target_position_side for p in open_positions)
            if max_positions == 0 or (not already_holds_symbol and len(open_positions) >= max_positions):
                return ShadowFillResult(False, None, None, "max_open_positions_reached",
                                         {"open_positions": len(open_positions), "max_open_positions": max_positions})

        # Emergency stop blocks entries but must never prevent risk-reducing closes.
        # Idempotent retries above return the original order without creating a duplicate.
        system_state = db.get(SystemState, 1)
        if system_state is not None and bool(system_state.emergency_stop) and not is_reducing:
            log_event(db, action="shadow.order_blocked_emergency_stop", severity="warning",
                      message="New shadow order blocked because the independent emergency stop is active.",
                      environment_mode=env, market_type=market, details={"symbol": symbol, "side": side})
            return ShadowFillResult(False, None, None, "emergency_stop_active", {"new_orders_blocked": True})

        pre = self.gateway.validate(symbol=symbol, market=mkt, side=side_normalized, order_type=order_type, quantity=quantity, price=price,
                                    reference_price=reference_price or price, idempotency_key=idem,
                                    reduce_only=reduce_only, verified_position_close=verified_position_close,
                                    leverage=leverage, environment_mode=env, live_enabled=False)
        if not pre.ok:
            log_event(db, action="shadow.order_rejected", severity="warning", message=pre.reason, environment_mode=env, market_type=market, details=pre.normalized)
            return ShadowFillResult(False, None, None, pre.reason, {"precheck": pre.to_dict()})
        effective_price = float(price or reference_price or 0.0)
        spot_position = None
        futures_position = None
        if mkt == MarketType.FUTURES:
            if not math.isfinite(effective_price) or effective_price <= 0:
                return ShadowFillResult(False, None, None, "futures_shadow_requires_reference_price", {})
            if reduce_only:
                position_side = "long" if side_normalized == "sell" else "short"
                futures_position = db.execute(select(PositionRecord).where(
                    PositionRecord.environment_mode == env, PositionRecord.market_type == market,
                    PositionRecord.symbol == symbol, PositionRecord.side == position_side,
                    PositionRecord.status == "open", PositionRecord.shadow.is_(True),
                )).scalar_one_or_none()
                available_qty = float(futures_position.quantity) if futures_position else 0.0
                if not futures_position or quantity > available_qty + 1e-10:
                    return ShadowFillResult(False, None, None, "reduce_only_position_or_quantity_invalid",
                                            {"available_quantity": available_qty, "requested_quantity": quantity})
                position_metadata = futures_position.metadata_json if isinstance(futures_position.metadata_json, dict) else {}
                raw_position_leverage = position_metadata.get("leverage", futures_position.leverage or 1.0)
                if isinstance(raw_position_leverage, bool):
                    return ShadowFillResult(False, None, None, "invalid_futures_position_leverage", {"orders_sent": False})
                try:
                    position_leverage = float(raw_position_leverage)
                except (TypeError, ValueError, OverflowError):
                    return ShadowFillResult(False, None, None, "invalid_futures_position_leverage", {"orders_sent": False})
                if not math.isfinite(position_leverage) or position_leverage < 1.0:
                    return ShadowFillResult(False, None, None, "invalid_futures_position_leverage", {"orders_sent": False})
                leverage = position_leverage
            else:
                target_side = "long" if side_normalized == "buy" else "short"
                open_symbol_positions = db.execute(select(PositionRecord).where(
                    PositionRecord.environment_mode == env, PositionRecord.market_type == market,
                    PositionRecord.symbol == symbol, PositionRecord.status == "open",
                    PositionRecord.shadow.is_(True),
                )).scalars().all()
                if any(position.side != target_side for position in open_symbol_positions):
                    return ShadowFillResult(False, None, None, "opposite_futures_position_open", {"symbol": symbol})
                futures_position = next((position for position in open_symbol_positions if position.side == target_side), None)
                if futures_position:
                    metadata = futures_position.metadata_json if isinstance(futures_position.metadata_json, dict) else {}
                    existing_leverage = float(metadata.get("leverage", 1.0))
                    if not math.isfinite(existing_leverage) or abs(existing_leverage - leverage) > 1e-9:
                        return ShadowFillResult(False, None, None, "futures_position_leverage_mismatch",
                                                {"existing_leverage": existing_leverage, "requested_leverage": leverage})
                    existing_notional = float(futures_position.mark_price or futures_position.entry_avg_price or 0.0) * float(futures_position.quantity or 0.0)
                else:
                    existing_notional = 0.0
                new_position_notional = existing_notional + quantity * effective_price
                if new_position_notional > settings.max_futures_trade_notional + 1e-8:
                    return ShadowFillResult(False, None, None, "futures_position_notional_exceeds_limit",
                                            {"position_notional": new_position_notional,
                                             "max_position_notional": settings.max_futures_trade_notional})
                # Enforce an aggregate portfolio exposure ceiling at the execution
                # boundary, not only a per-symbol/per-position cap.
                try:
                    exposure_multiple = float(settings.max_portfolio_exposure_multiple)
                    initial_equity = float(self._initial_cash(db, env=env))
                    existing_exposure = 0.0
                    for position in db.execute(select(PositionRecord).where(
                        PositionRecord.environment_mode == env,
                        PositionRecord.market_type == market,
                        PositionRecord.status == "open",
                        PositionRecord.shadow.is_(True),
                    )).scalars().all():
                        position_price = float(position.mark_price or position.entry_avg_price or 0.0)
                        position_quantity = float(position.quantity or 0.0)
                        if (not math.isfinite(position_price) or not math.isfinite(position_quantity)
                                or position_price <= 0 or position_quantity <= 0):
                            return ShadowFillResult(False, None, None, "invalid_existing_portfolio_exposure",
                                                    {"orders_sent": False})
                        existing_exposure += abs(position_price * position_quantity)
                except (TypeError, ValueError, OverflowError):
                    return ShadowFillResult(False, None, None, "invalid_portfolio_exposure_limit",
                                            {"orders_sent": False})
                if (not math.isfinite(exposure_multiple) or exposure_multiple <= 0
                        or not math.isfinite(initial_equity) or initial_equity <= 0
                        or not math.isfinite(existing_exposure)):
                    return ShadowFillResult(False, None, None, "invalid_portfolio_exposure_limit",
                                            {"orders_sent": False})
                max_portfolio_exposure = initial_equity * exposure_multiple
                projected_portfolio_exposure = existing_exposure + quantity * effective_price
                if projected_portfolio_exposure > max_portfolio_exposure + 1e-8:
                    return ShadowFillResult(False, None, None, "max_portfolio_exposure_exceeded",
                                            {"current_exposure": round(existing_exposure, 8),
                                             "projected_exposure": round(projected_portfolio_exposure, 8),
                                             "max_portfolio_exposure": round(max_portfolio_exposure, 8)})
                required_margin = (quantity * effective_price / leverage) + (quantity * effective_price * max(0.0, fee_rate))
                available_margin = self._cash_balance(db, env=env, market=market)
                if required_margin > available_margin + 1e-8:
                    return ShadowFillResult(False, None, None, "insufficient_demo_futures_margin",
                                            {"available_margin": available_margin, "required_margin": round(required_margin, 8)})
        if mkt == MarketType.SPOT:
            if effective_price <= 0:
                return ShadowFillResult(False, None, None, "spot_shadow_requires_reference_price", {})
            if side.lower() == "buy":
                available = self._cash_balance(db, env=env, market=market)
                required = quantity * effective_price * (1.0 + max(0.0, fee_rate))
                if required > available + 1e-8:
                    return ShadowFillResult(False, None, None, "insufficient_demo_quote_balance", {"available": available, "required": round(required, 8)})
            elif side.lower() == "sell":
                spot_position = db.execute(select(PositionRecord).where(
                    PositionRecord.environment_mode == env, PositionRecord.market_type == market,
                    PositionRecord.symbol == symbol, PositionRecord.side == "long_spot",
                    PositionRecord.status == "open", PositionRecord.shadow.is_(True)
                )).scalar_one_or_none()
                available_qty = float(spot_position.quantity) if spot_position else 0.0
                if not spot_position or quantity > available_qty + 1e-10:
                    return ShadowFillResult(False, None, None, "insufficient_demo_base_asset", {"available_quantity": available_qty, "requested_quantity": quantity})
        order = OrderRecord(environment_mode=env, market_type=market, symbol=symbol, side=side_normalized, order_type=order_type.lower(),
                            quantity=quantity, price=price, status="submitted", shadow=True, idempotency_key=idem, client_order_id=f"sim-{idem[:24]}",
                            reason="shadow simulation", metadata_json={
                                "precheck": pre.to_dict(), "simulate": simulate,
                                "leverage": leverage, "reduce_only": reduce_only,
                            })
        db.add(order); db.flush()
        if simulate == "connection_loss":
            order.status = "submitted"
            order.reason = "shadow submitted; connection loss simulated - reconciliation required before retry"
            db.add(order); self._snapshot(db, env=env, market=market)
            log_event(db, action="shadow.connection_loss", severity="warning", message="Shadow order submitted with simulated connection loss.", entity_type="order", entity_id=str(order.id))
            return ShadowFillResult(True, order, None, "connection_loss_simulated", {"reconcile_required": True, "idempotency_key": idem})
        if simulate == "cancel":
            order.status = "cancelled"; order.reason = "shadow cancelled"
            db.add(order); self._snapshot(db, env=env, market=market)
            log_event(db, action="shadow.cancelled", entity_type="order", entity_id=str(order.id))
            return ShadowFillResult(True, order, None, "cancelled", {})
        if simulate == "reject":
            order.status = "rejected"; order.reason = "shadow rejected by simulator"
            db.add(order); self._snapshot(db, env=env, market=market)
            return ShadowFillResult(False, order, None, "rejected", {})
        # fill / partial
        ratio = fill_ratio
        fill_px = effective_price
        filled = quantity * ratio
        order.filled_quantity = filled
        order.avg_fill_price = fill_px if filled > 0 else None
        order.status = "filled" if ratio >= 1.0 else ("partially_filled" if ratio > 0 else "submitted")
        notional = filled * fill_px
        order.fees_paid = round(notional * max(0.0, fee_rate), 8)
        db.add(order)
        pos = None
        if filled > 0 and fill_px > 0:
            if mkt == MarketType.SPOT and side.lower() == "sell":
                pos = spot_position
                prev_qty = float(pos.quantity)
                avg_entry = float(pos.entry_avg_price or fill_px)
                realized_gross = (fill_px - avg_entry) * filled
                pos_meta = dict(pos.metadata_json or {})
                remaining_entry_fees = float(pos_meta.get("entry_fees_remaining", avg_entry * prev_qty * max(0.0, fee_rate)))
                entry_fee_allocated = remaining_entry_fees * min(1.0, filled / prev_qty) if prev_qty > 0 else 0.0
                pos_meta["entry_fees_remaining"] = round(max(0.0, remaining_entry_fees - entry_fee_allocated), 8)
                pos.metadata_json = pos_meta
                realized_net = realized_gross - entry_fee_allocated - order.fees_paid
                order.metadata_json = {**(order.metadata_json or {}), "realized_pnl_gross": round(realized_gross, 8),
                                       "entry_fee_allocated": round(entry_fee_allocated, 8),
                                       "realized_pnl_net": round(realized_net, 8)}
                pos.realized_pnl += round(realized_gross, 8)
                pos.quantity = max(0.0, prev_qty - filled)
                pos.mark_price = mark_price or fill_px
                pos.fees_total += order.fees_paid
                pos.unrealized_pnl = round((pos.mark_price - avg_entry) * pos.quantity, 8)
                if pos.quantity <= 1e-10:
                    pos.quantity = 0.0
                    pos.status = "closed"
                    pos.unrealized_pnl = 0.0
            elif mkt == MarketType.FUTURES and reduce_only:
                pos = futures_position
                prev_qty = float(pos.quantity)
                avg_entry = float(pos.entry_avg_price or fill_px)
                direction = 1.0 if pos.side == "long" else -1.0
                realized_gross = direction * (fill_px - avg_entry) * filled
                pos_meta = dict(pos.metadata_json or {})
                remaining_entry_fees = float(pos_meta.get("entry_fees_remaining", avg_entry * prev_qty * max(0.0, fee_rate)))
                entry_fee_allocated = remaining_entry_fees * min(1.0, filled / prev_qty) if prev_qty > 0 else 0.0
                pos_meta["entry_fees_remaining"] = round(max(0.0, remaining_entry_fees - entry_fee_allocated), 8)
                pos.metadata_json = pos_meta
                realized_net = realized_gross - entry_fee_allocated - order.fees_paid
                order.metadata_json = {
                    **(order.metadata_json or {}),
                    "realized_pnl_gross": round(realized_gross, 8),
                    "entry_fee_allocated": round(entry_fee_allocated, 8),
                    "realized_pnl_net": round(realized_net, 8),
                }
                pos.realized_pnl += round(realized_gross, 8)
                pos.quantity = max(0.0, prev_qty - filled)
                pos.mark_price = mark_price or fill_px
                pos.fees_total += order.fees_paid
                pos.unrealized_pnl = round(direction * (pos.mark_price - avg_entry) * pos.quantity, 8)
                if pos.quantity <= 1e-10:
                    pos.quantity = 0.0
                    pos.status = "closed"
                    pos.unrealized_pnl = 0.0
                else:
                    pos_meta["initial_margin_estimate"] = round((avg_entry * pos.quantity) / leverage, 8)
                    pos.metadata_json = pos_meta
            else:
                pos_side = "long_spot" if mkt == MarketType.SPOT else ("long" if side_normalized == "buy" else "short")
                pos = self._get_or_create_position(db, env=env, market=market, symbol=symbol, side=pos_side, shadow=True)
                prev_qty = float(pos.quantity or 0.0); prev_avg = float(pos.entry_avg_price or 0.0)
                new_qty = prev_qty + filled
                pos.entry_avg_price = ((prev_avg * prev_qty) + (fill_px * filled)) / new_qty if new_qty else fill_px
                pos.quantity = new_qty
                pos.mark_price = mark_price or fill_px
                pos_meta = dict(pos.metadata_json or {})
                pos_meta["entry_fees_remaining"] = round(float(pos_meta.get("entry_fees_remaining", 0.0)) + order.fees_paid, 8)
                if mkt == MarketType.FUTURES:
                    pos_meta["leverage"] = leverage
                    pos_meta["margin_mode"] = "isolated"
                    pos_meta["initial_margin_estimate"] = round((float(pos.entry_avg_price) * new_qty) / leverage, 8)
                    pos_meta["maintenance_margin_rate_estimate"] = 0.005
                    pos.leverage = leverage
                pos.metadata_json = pos_meta
                pos.fees_total += order.fees_paid
                if pos.mark_price and pos.entry_avg_price:
                    direction = 1.0 if pos_side in {"long_spot", "long", "buy"} else -1.0
                    pos.unrealized_pnl = round(direction * (pos.mark_price - pos.entry_avg_price) * pos.quantity, 8)
            db.add(pos)
        snap = self._snapshot(db, env=env, market=market)
        log_event(db, action="shadow.order_filled" if ratio >= 1 else "shadow.order_partial", entity_type="order", entity_id=str(order.id),
                  details={"filled": filled, "avg_fill_price": fill_px, "fees": order.fees_paid, "snapshot_id": snap.id})
        return ShadowFillResult(True, order, pos, order.status, {"snapshot_id": snap.id, "fees_paid": order.fees_paid})

    def record_snapshot(self, db: Session, *, env: str, market: str) -> PortfolioSnapshot:
        """Persist one portfolio valuation at a state-changing event, never on read."""
        return self._snapshot(db, env=env, market=market)

    def get_portfolio(self, db: Session, *, env: str, market: str) -> dict[str, Any]:
        positions = db.execute(select(PositionRecord).where(
            PositionRecord.environment_mode == env, PositionRecord.market_type == market,
            PositionRecord.status == "open", PositionRecord.shadow.is_(True),
        )).scalars().all()
        orders = db.execute(select(OrderRecord).where(
            OrderRecord.environment_mode == env, OrderRecord.market_type == market,
            OrderRecord.shadow.is_(True),
        ).order_by(OrderRecord.id.desc()).limit(50)).scalars().all()
        all_positions = db.execute(select(PositionRecord).where(
            PositionRecord.environment_mode == env, PositionRecord.market_type == market,
            PositionRecord.shadow.is_(True),
        )).scalars().all()
        cash_balance = self._cash_balance(db, env=env, market=market)
        unrealized = sum(float(position.unrealized_pnl or 0.0) for position in positions)
        realized = sum(float(position.realized_pnl or 0.0) for position in all_positions)
        fees = sum(float(order.fees_paid or 0.0) for order in self._orders(db, env=env, market=market))
        exposure = sum(abs((position.mark_price or position.entry_avg_price or 0.0) * position.quantity) for position in positions)
        if market == "spot":
            market_value = sum((position.mark_price or position.entry_avg_price or 0.0) * position.quantity for position in positions)
            equity = cash_balance + market_value
        elif market == "futures":
            equity = self._initial_cash(db, env=env) + unrealized + realized - fees
        else:
            equity = cash_balance + unrealized + realized
        snapshot = {
            "id": None, "cash_balance": cash_balance, "equity_estimate": equity,
            "unrealized_pnl": unrealized, "realized_pnl": realized,
            "fees_total": fees, "exposure_notional": exposure,
            "positions_open": len(positions),
        }
        return {
            "snapshot": snapshot,
            "positions": [{"id": p.id, "symbol": p.symbol, "side": p.side, "quantity": p.quantity, "entry_avg_price": p.entry_avg_price, "mark_price": p.mark_price,
                           "unrealized_pnl": p.unrealized_pnl, "realized_pnl": p.realized_pnl, "fees_total": p.fees_total,
                           "leverage": float(p.leverage or 1.0),
                           "status": p.status, "shadow": p.shadow} for p in positions],
            "orders": [{"id": o.id, "symbol": o.symbol, "side": o.side, "type": o.order_type, "quantity": o.quantity, "filled_quantity": o.filled_quantity,
                        "price": o.price, "avg_fill_price": o.avg_fill_price, "status": o.status, "shadow": o.shadow, "fees_paid": o.fees_paid, "idempotency_key": o.idempotency_key} for o in orders],
        }


    def close_shadow_position(self, db: Session, *, position_id: int, close_price: float, fee_rate: float = 0.001) -> dict[str, Any]:
        if isinstance(position_id, bool) or not isinstance(position_id, int) or position_id <= 0:
            return {"ok": False, "reason": "invalid_position_id"}
        pos = db.get(PositionRecord, position_id)
        if not pos or pos.status != "open" or pos.quantity <= 0 or pos.shadow is not True:
            return {"ok": False, "reason": "open_shadow_position_required"}
        if pos.market_type not in {MarketType.SPOT.value, MarketType.FUTURES.value}:
            return {"ok": False, "reason": "unsupported_shadow_market"}
        if isinstance(close_price, bool) or isinstance(fee_rate, bool):
            return {"ok": False, "reason": "boolean_close_numeric_input_not_allowed"}
        try:
            close_price = float(close_price)
            fee_rate = float(fee_rate)
            position_quantity = float(pos.quantity)
            position_leverage = float((pos.metadata_json or {}).get("leverage", 1.0)) if isinstance(pos.metadata_json, dict) else 1.0
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "reason": "invalid_close_numeric_input"}
        if not all(math.isfinite(value) for value in (close_price, fee_rate, position_quantity, position_leverage)):
            return {"ok": False, "reason": "non_finite_close_numeric_input"}
        if close_price <= 0 or position_quantity <= 0 or not 0.0 <= fee_rate <= 0.01:
            return {"ok": False, "reason": "invalid_close_price_quantity_or_fee"}
        if position_leverage < 1.0 or (pos.market_type == MarketType.SPOT.value and position_leverage != 1.0):
            return {"ok": False, "reason": "invalid_position_leverage"}
        side = "sell" if pos.market_type == "spot" or pos.side in {"long_spot", "long", "buy"} else "buy"
        close_key = f"shadow-close-{pos.id}-{round(position_quantity, 12)}-{round(close_price, 12)}"
        result = self.create_shadow_order(
            db, env=pos.environment_mode, market=pos.market_type, symbol=pos.symbol, side=side,
            order_type="market", quantity=position_quantity, price=close_price, reference_price=close_price,
            idempotency_key=close_key, simulate="fill", fee_rate=fee_rate, mark_price=close_price,
            leverage=position_leverage, reduce_only=pos.market_type == MarketType.FUTURES.value,
            verified_position_close=pos.market_type == MarketType.SPOT.value,
        )
        if not result.ok:
            return {"ok": False, "reason": result.message, "details": result.details}
        snap = self._snapshot(db, env=pos.environment_mode, market=pos.market_type)
        log_event(db, action="shadow.position_closed", entity_type="position", entity_id=str(pos.id),
                  details={"close_price": close_price, "order_id": result.order.id if result.order else None, "snapshot_id": snap.id})
        return {"ok": True, "position_id": pos.id, "order_id": result.order.id if result.order else None,
                "realized_pnl": pos.realized_pnl, "fee": result.order.fees_paid if result.order else 0.0, "snapshot_id": snap.id}
