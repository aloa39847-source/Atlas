"""Realized demo-trade outcome ledger for strategy-level learning.

Only fully closed demo positions are recorded. The ledger is observational:
it never tunes parameters, changes active versions, or authorizes orders.
"""
from __future__ import annotations

import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import LearningRecord, OrderRecord, PositionRecord


def record_closed_demo_trade(
    db: Session,
    position: PositionRecord,
    exit_order: OrderRecord,
    *,
    exit_reason: str,
) -> dict[str, Any]:
    if position.environment_mode != "demo":
        return {"ok": False, "error": "demo_environment_required"}
    if position.status != "closed" or float(position.quantity or 0.0) > 1e-12:
        return {"ok": False, "error": "fully_closed_position_required"}
    if exit_order.status != "filled":
        return {"ok": False, "error": "confirmed_full_exit_fill_required"}
    existing = db.execute(select(LearningRecord.id).where(
        LearningRecord.category == "trade_outcome",
        LearningRecord.subject == str(position.id),
    ).limit(1)).first()
    if existing:
        return {"ok": True, "already_recorded": True, "record_id": existing[0]}

    metadata = position.metadata_json if isinstance(position.metadata_json, dict) else {}
    entry_order = None
    entry_id = metadata.get("entry_order_id")
    if entry_id is not None:
        try:
            entry_order = db.get(OrderRecord, int(entry_id))
        except (TypeError, ValueError, OverflowError):
            entry_order = None
    try:
        gross = float(position.realized_pnl or 0.0)
        entry_fee = float(entry_order.fees_paid or 0.0) if entry_order else 0.0
        exit_fee = float(exit_order.fees_paid or 0.0)
    except (TypeError, ValueError, OverflowError):
        return {"ok": False, "error": "invalid_realized_trade_accounting"}
    if not all(math.isfinite(value) and value >= 0 for value in (entry_fee, exit_fee)) or not math.isfinite(gross):
        return {"ok": False, "error": "non_finite_realized_trade_accounting"}
    fees = entry_fee + exit_fee
    net = gross - fees
    strategy = str(metadata.get("strategy") or "unattributed")
    evidence = {
        "position_id": position.id,
        "exit_order_id": exit_order.id,
        "entry_order_id": entry_order.id if entry_order else None,
        "strategy_key": strategy,
        "symbol": position.symbol,
        "market_type": position.market_type,
        "market_regime": str(metadata.get("market_regime") or metadata.get("regime") or "unknown"),
        "timeframe": str(metadata.get("timeframe") or "unknown"),
        "side": position.side,
        "entry_price": float(position.entry_avg_price or 0.0),
        "exit_price": float(exit_order.avg_fill_price or 0.0),
        "quantity_closed": float(exit_order.filled_quantity or 0.0),
        "gross_pnl": round(gross, 10),
        "entry_fees": round(entry_fee, 10),
        "exit_fees": round(exit_fee, 10),
        "total_fees": round(fees, 10),
        "net_pnl": round(net, 10),
        "exit_reason": str(exit_reason or "unspecified"),
        "source": "binance_demo_confirmed_fills",
        "research_only": True,
    }
    record = LearningRecord(
        environment_mode="demo",
        market_type=position.market_type,
        category="trade_outcome",
        subject=str(position.id),
        outcome="win" if net > 0 else "loss" if net < 0 else "flat",
        lesson="Realized, fee-adjusted Binance Demo position outcome; research record only.",
        evidence=evidence,
        verified=False,
    )
    db.add(record)
    exit_meta = dict(exit_order.metadata_json or {})
    exit_meta.update({
        "position_id": position.id,
        "strategy": strategy,
        "realized_pnl_gross": round(gross, 10),
        "realized_pnl_net": round(net, 10),
        "realized_fees_total": round(fees, 10),
        "exit_reason": str(exit_reason or "unspecified"),
    })
    exit_order.metadata_json = exit_meta
    db.add(exit_order)
    db.flush()
    return {"ok": True, "record_id": record.id, "strategy_key": strategy, "net_pnl": round(net, 10)}


def summarize_strategy_outcomes(db: Session, strategy_key: str) -> dict[str, Any]:
    """Return auditable, fee-adjusted strategy learning metrics from confirmed demo fills."""
    key = str(strategy_key or "").strip()
    if not key:
        return {"ok": False, "error": "strategy_key_required"}
    rows = db.execute(select(LearningRecord).where(
        LearningRecord.category == "trade_outcome",
        LearningRecord.environment_mode == "demo",
    ).order_by(LearningRecord.created_at, LearningRecord.id)).scalars().all()
    outcomes: list[dict[str, Any]] = []
    for row in rows:
        evidence = row.evidence if isinstance(row.evidence, dict) else {}
        # Only accept records emitted by the confirmed Demo fill ledger. Other
        # LearningRecord categories may be notes, imported examples, or tests.
        if (evidence.get("source") != "binance_demo_confirmed_fills"
                or evidence.get("research_only") is not True
                or evidence.get("strategy_key") != key):
            continue
        try:
            pnl = float(evidence["net_pnl"])
            fees = float(evidence.get("total_fees", 0.0))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(pnl) or not math.isfinite(fees) or fees < 0:
            continue
        outcomes.append({
            "net_pnl": pnl,
            "fees": fees,
            "market_type": str(evidence.get("market_type") or "unknown"),
            "symbol": str(evidence.get("symbol") or "unknown"),
            "market_regime": str(evidence.get("market_regime") or "unknown"),
            "timeframe": str(evidence.get("timeframe") or "unknown"),
        })

    values = [item["net_pnl"] for item in outcomes]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    equity = peak = drawdown = 0.0
    current_loss_streak = longest_loss_streak = 0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        if value < 0:
            current_loss_streak += 1
            longest_loss_streak = max(longest_loss_streak, current_loss_streak)
        else:
            current_loss_streak = 0

    def breakdown(field: str) -> dict[str, dict[str, Any]]:
        groups: dict[str, list[float]] = {}
        for item in outcomes:
            groups.setdefault(item[field], []).append(item["net_pnl"])
        return {
            name: {
                "sample_count": len(group),
                "wins": sum(value > 0 for value in group),
                "losses": sum(value < 0 for value in group),
                "net_pnl": round(sum(group), 10),
                "expectancy_per_trade": round(sum(group) / len(group), 10),
            }
            for name, group in sorted(groups.items())
        }

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    recent = values[-10:]
    average_win = sum(wins) / len(wins) if wins else 0.0
    average_loss = sum(losses) / len(losses) if losses else 0.0
    sample_count = len(values)
    expectancy = sum(values) / sample_count if sample_count else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss else (None if not gross_profit else float("inf"))
    recent_expectancy = sum(recent) / len(recent) if recent else 0.0
    previous_values = values[-20:-10]
    previous_expectancy = sum(previous_values) / len(previous_values) if previous_values else None
    if sample_count < 30:
        research_guidance = {
            "status": "insufficient_sample",
            "message": "Collect at least 30 confirmed demo closes before evaluating strategy changes.",
        }
    elif expectancy <= 0 and (profit_factor is None or profit_factor < 1.0):
        research_guidance = {
            "status": "negative_expectancy_review",
            "message": "Re-test this strategy by market regime and timeframe; do not increase its capital allocation.",
        }
    elif sample_count >= 20 and recent_expectancy < 0 and previous_expectancy is not None and previous_expectancy > 0:
        research_guidance = {
            "status": "recent_deterioration",
            "message": "Recent results have weakened; pause parameter changes and inspect the latest market regimes.",
        }
    elif longest_loss_streak >= 5:
        research_guidance = {
            "status": "loss_streak_review",
            "message": "Review consecutive losses and risk sizing before any strategy version is promoted.",
        }
    else:
        research_guidance = {
            "status": "continue_observation",
            "message": "Continue collecting demo outcomes; no automatic parameter change is recommended.",
        }

    return {
        "ok": True,
        "strategy_key": key,
        "sample_count": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "flats": sum(value == 0 for value in values),
        "win_rate": round(len(wins) / len(values), 6) if values else 0.0,
        "net_pnl": round(sum(values), 10),
        "expectancy_per_trade": round(expectancy, 10),
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss else (None if not gross_profit else "infinite"),
        "average_win": round(average_win, 10),
        "average_loss": round(average_loss, 10),
        "payoff_ratio": round(average_win / abs(average_loss), 6) if average_loss else (None if not average_win else "infinite"),
        "largest_win": round(max(wins), 10) if wins else 0.0,
        "largest_loss": round(min(losses), 10) if losses else 0.0,
        "fees_paid": round(sum(item["fees"] for item in outcomes), 10),
        "max_drawdown_net_pnl": round(drawdown, 10),
        "longest_loss_streak": longest_loss_streak,
        "recent_10_expectancy": round(recent_expectancy, 10),
        "recent_30_expectancy": round(sum(values[-30:]) / len(values[-30:]), 10) if values else 0.0,
        "previous_10_expectancy": round(sum(values[-20:-10]) / len(values[-20:-10]), 10) if len(values) >= 20 else None,
        "market_breakdown": breakdown("market_type"),
        "symbol_breakdown": breakdown("symbol"),
        "regime_breakdown": breakdown("market_regime"),
        "timeframe_breakdown": breakdown("timeframe"),
        "sample_sufficient_for_research": sample_count >= 30,
        "research_guidance": research_guidance,
        "source": "binance_demo_confirmed_fills",
        "research_only": True,
        "auto_tuning": False,
        "live_config_changed": False,
    }


def build_strategy_memory_context(db: Session, *, limit: int = 500) -> dict[str, dict[str, Any]]:
    """Build compact strategy memory from confirmed, realized Demo closes only.

    The result is observational input for advisory agents. It never changes
    strategy parameters, risk limits, or order permissions.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
        return {}
    rows = db.execute(select(LearningRecord).where(
        LearningRecord.category == "trade_outcome",
        LearningRecord.environment_mode == "demo",
    ).order_by(LearningRecord.created_at.desc(), LearningRecord.id.desc()).limit(limit)).scalars().all()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in reversed(rows):
        evidence = row.evidence if isinstance(row.evidence, dict) else {}
        if evidence.get("source") != "binance_demo_confirmed_fills" or evidence.get("research_only") is not True:
            continue
        key = evidence.get("strategy_key")
        if not isinstance(key, str) or not key.strip():
            continue
        try:
            pnl = float(evidence["net_pnl"])
            fees = float(evidence.get("total_fees", 0.0))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(pnl) or not math.isfinite(fees) or fees < 0:
            continue
        grouped.setdefault(key, []).append({
            "net_pnl": pnl,
            "fees": fees,
            "symbol": str(evidence.get("symbol") or "unknown"),
            "market_regime": str(evidence.get("market_regime") or "unknown"),
            "timeframe": str(evidence.get("timeframe") or "unknown"),
        })

    memory: dict[str, dict[str, Any]] = {}
    for key, items in grouped.items():
        values = [item["net_pnl"] for item in items]
        wins = sum(value > 0 for value in values)
        losses = sum(value < 0 for value in values)
        recent = values[-10:]
        equity = peak = max_drawdown = 0.0
        for value in values:
            equity += value
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
        count = len(values)
        memory[key] = {
            "sample_count": count,
            "wins": wins,
            "losses": losses,
            "flats": count - wins - losses,
            "win_rate": round(wins / count, 6),
            "net_pnl": round(sum(values), 10),
            "expectancy_per_trade": round(sum(values) / count, 10),
            "recent_10_expectancy": round(sum(recent) / len(recent), 10),
            "fees_paid": round(sum(item["fees"] for item in items), 10),
            "max_drawdown_net_pnl": round(max_drawdown, 10),
            "research_guidance": (
                "insufficient_sample" if count < 30 else
                "negative_expectancy_review" if sum(values) / count <= 0 else
                "continue_observation"
            ),
            "source": "confirmed_demo_trade_outcomes",
            "research_only": True,
            "auto_tuning": False,
        }
    return memory
