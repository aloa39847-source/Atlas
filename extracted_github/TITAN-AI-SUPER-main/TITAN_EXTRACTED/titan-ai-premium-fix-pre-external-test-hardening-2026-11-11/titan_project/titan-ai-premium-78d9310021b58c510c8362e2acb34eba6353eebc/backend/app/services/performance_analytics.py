"""Shadow-only portfolio performance analytics."""
from __future__ import annotations

import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import OrderRecord, PortfolioSnapshot, PositionRecord, SystemState


class PerformanceAnalyticsService:
    def evaluate(self, db: Session, *, environment_mode: str = "demo", market_type: str = "spot") -> dict[str, Any]:
        if environment_mode not in {"demo", "live"} or market_type not in {"spot", "futures"}:
            return {"ok": False, "error": "invalid_performance_scope"}
        orders = db.execute(
            select(OrderRecord).where(
                OrderRecord.environment_mode == environment_mode,
                OrderRecord.market_type == market_type,
                OrderRecord.shadow.is_(True),
            ).order_by(OrderRecord.created_at, OrderRecord.id)
        ).scalars().all()
        positions = db.execute(
            select(PositionRecord).where(
                PositionRecord.environment_mode == environment_mode,
                PositionRecord.market_type == market_type,
                PositionRecord.shadow.is_(True),
            )
        ).scalars().all()
        snapshots = db.execute(
            select(PortfolioSnapshot).where(
                PortfolioSnapshot.environment_mode == environment_mode,
                PortfolioSnapshot.market_type == market_type,
            ).order_by(PortfolioSnapshot.created_at, PortfolioSnapshot.id)
        ).scalars().all()

        state = db.get(SystemState, 1)
        limits = (getattr(state, "risk_limits", None) or {}) if state else {}
        try:
            initial_cash = float(limits.get("demo_initial_balance", 150.0))
        except (TypeError, ValueError, OverflowError):
            initial_cash = 150.0
        if not math.isfinite(initial_cash) or initial_cash <= 0:
            initial_cash = 150.0

        realized_fills = []
        total_fees = 0.0
        total_volume = 0.0
        for order in orders:
            try:
                fees = float(order.fees_paid or 0.0)
                quantity = float(order.filled_quantity or 0.0)
                price = float(order.avg_fill_price or order.price or 0.0)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(fees) and fees >= 0:
                total_fees += fees
            if math.isfinite(quantity) and quantity > 0 and math.isfinite(price) and price > 0:
                notional = quantity * price
                if math.isfinite(notional):
                    total_volume += notional
            metadata = order.metadata_json or {}
            is_realized_exit = (
                order.side.lower() == "sell" if market_type == "spot"
                else metadata.get("reduce_only") is True
            )
            if is_realized_exit and order.status in {"filled", "partially_filled"}:
                try:
                    pnl = float(metadata["realized_pnl_net"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
                if math.isfinite(pnl):
                    realized_fills.append({
                        "order_id": order.id,
                        "symbol": order.symbol,
                        "created_at": order.created_at.isoformat() if order.created_at else None,
                        "realized_pnl_net": pnl,
                    })

        pnl_values = [item["realized_pnl_net"] for item in realized_fills]
        wins = [value for value in pnl_values if value > 0]
        losses = [value for value in pnl_values if value < 0]
        total_realized = sum(pnl_values)
        unrealized_values = []
        open_positions = []
        for position in positions:
            if position.status != "open":
                continue
            try:
                unrealized = float(position.unrealized_pnl or 0.0)
                quantity = float(position.quantity or 0.0)
                mark = float(position.mark_price or position.entry_avg_price or 0.0)
            except (TypeError, ValueError, OverflowError):
                continue
            if not all(math.isfinite(value) for value in (unrealized, quantity, mark)) or quantity <= 0:
                continue
            unrealized_values.append(unrealized)
            open_positions.append({
                "symbol": position.symbol,
                "side": position.side,
                "quantity": quantity,
                "mark_price": mark,
                "unrealized_pnl": unrealized,
            })
        total_unrealized = sum(unrealized_values)

        equity_values = []
        for snapshot in snapshots:
            try:
                equity = float(snapshot.equity_estimate)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(equity) and equity > 0:
                equity_values.append(equity)
        if equity_values:
            peak = equity_values[0]
            max_drawdown = 0.0
            for equity in equity_values:
                peak = max(peak, equity)
                if peak > 0:
                    max_drawdown = max(max_drawdown, (peak - equity) / peak)
            current_equity = equity_values[-1]
        else:
            max_drawdown = None
            current_equity = initial_cash + total_realized + total_unrealized

        total_fills = len(realized_fills)
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        return {
            "ok": True,
            "scope": {"environment_mode": environment_mode, "market_type": market_type, "shadow_only": True},
            "initial_cash": round(initial_cash, 8),
            "current_equity_estimate": round(current_equity, 8),
            "net_return_percent": round(((current_equity / initial_cash) - 1.0) * 100.0, 6),
            "realized_pnl_net": round(total_realized, 8),
            "unrealized_pnl": round(total_unrealized, 8),
            "total_fees_paid": round(total_fees, 8),
            "traded_notional": round(total_volume, 8),
            "realized_exit_fills": total_fills,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / total_fills, 4) if total_fills else 0.0,
            "average_win": round(sum(wins) / len(wins), 8) if wins else 0.0,
            "average_loss": round(sum(losses) / len(losses), 8) if losses else 0.0,
            "expectancy_per_exit_fill": round(total_realized / total_fills, 8) if total_fills else 0.0,
            "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else (None if gross_profit == 0 else "infinite"),
            "max_drawdown_from_snapshots": round(max_drawdown, 6) if max_drawdown is not None else None,
            "snapshot_count": len(equity_values),
            "open_positions": open_positions,
            "realized_fills": realized_fills[-100:],
            "data_scope": "internal_demo_shadow_records_only",
            "note": "Metrics describe the local Shadow ledger only. They are not live exchange performance, and exit fills are not necessarily independent round-trip trades.",
        }
