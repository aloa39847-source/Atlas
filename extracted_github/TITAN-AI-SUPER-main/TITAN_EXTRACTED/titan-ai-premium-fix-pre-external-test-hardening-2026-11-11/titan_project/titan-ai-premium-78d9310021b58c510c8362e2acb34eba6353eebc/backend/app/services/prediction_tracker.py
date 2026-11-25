"""Mark-to-market evaluation of matured analysis decisions (not realized trades)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any

from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from app.engines.market_data import MarketDataEngine
from app.models.entities import DecisionRecord, LearningRecord


class PredictionTrackerService:
    def __init__(self, market_data: MarketDataEngine | None = None) -> None:
        self.market_data = market_data or MarketDataEngine()

    def evaluate_matured(
        self,
        db: Session,
        *,
        environment_mode: str = "demo",
        horizon_seconds: int = 3600,
        max_records: int = 200,
    ) -> dict[str, Any]:
        if environment_mode not in {"demo", "live"}:
            return {"ok": False, "error": "invalid_environment_mode"}
        if isinstance(horizon_seconds, bool) or not isinstance(horizon_seconds, int) or not 60 <= horizon_seconds <= 30 * 86400:
            return {"ok": False, "error": "horizon_seconds_out_of_range"}
        if isinstance(max_records, bool) or not isinstance(max_records, int) or not 1 <= max_records <= 500:
            return {"ok": False, "error": "max_records_out_of_range"}

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=horizon_seconds)
        already_evaluated = select(LearningRecord.id).where(
            LearningRecord.category == "prediction_outcome",
            LearningRecord.subject == cast(DecisionRecord.id, String),
        ).exists()
        decisions = db.execute(
            select(DecisionRecord).where(
                DecisionRecord.environment_mode == environment_mode,
                DecisionRecord.data_source.in_(("binance_public", "binance_futures_public", "live_public")),
                DecisionRecord.market_type.in_(("spot", "futures")),
                DecisionRecord.action.in_(("buy", "long", "short", "sell")),
                ~already_evaluated,
            ).order_by(DecisionRecord.created_at, DecisionRecord.id).limit(max_records)
        ).scalars().all()

        pending = []
        skipped = []
        for decision in decisions:
            created = decision.created_at
            if created is None:
                skipped.append({"decision_id": decision.id, "reason": "missing_created_at"})
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created > cutoff:
                skipped.append({"decision_id": decision.id, "reason": "prediction_horizon_not_matured"})
                continue
            existing = db.execute(
                select(LearningRecord.id).where(
                    LearningRecord.category == "prediction_outcome",
                    LearningRecord.subject == str(decision.id),
                ).limit(1)
            ).first()
            if existing:
                skipped.append({"decision_id": decision.id, "reason": "already_evaluated"})
                continue
            entry = decision.entry_price
            try:
                entry = float(entry)
            except (TypeError, ValueError, OverflowError):
                entry = 0.0
            if not math.isfinite(entry) or entry <= 0:
                skipped.append({"decision_id": decision.id, "reason": "invalid_entry_price"})
                continue
            # SELL is an exit/reduction action, not an open directional
            # prediction. SHORT is the explicit futures short-entry action.
            action = str(decision.action).lower()
            if action == "sell":
                reason = "spot_sell_is_not_a_short_prediction" if decision.market_type == "spot" else "sell_action_is_not_a_short_entry"
                skipped.append({"decision_id": decision.id, "reason": reason})
                continue
            pending.append((decision, entry))

        if not pending:
            return {"ok": True, "evaluated": 0, "skipped": skipped, "items": [], "note": "No matured unevaluated directional predictions."}
        symbols_by_market: dict[str, set[str]] = {"spot": set(), "futures": set()}
        for decision, _ in pending:
            symbols_by_market[str(decision.market_type)].add(decision.symbol)
        prices_by_market: dict[str, dict[str, float]] = {}
        try:
            for market_type, symbols in symbols_by_market.items():
                if symbols:
                    prices_by_market[market_type] = self.market_data.get_public_last_prices(
                        symbols, market_type=market_type,
                    )
        except Exception as exc:
            return {"ok": False, "error": "public_mark_prices_unavailable", "detail": str(exc)[:200],
                    "evaluated": 0, "skipped": skipped}
        evaluated = []
        for decision, entry in pending:
            # Direction belongs to this decision, not the final item visited
            # while building the pending batch.
            action = str(decision.action).lower()
            raw_price = prices_by_market.get(str(decision.market_type), {}).get(decision.symbol)
            try:
                price = float(raw_price)
            except (TypeError, ValueError, OverflowError):
                skipped.append({"decision_id": decision.id, "reason": "fresh_public_price_unavailable"})
                continue
            if not math.isfinite(price) or price <= 0:
                skipped.append({"decision_id": decision.id, "reason": "invalid_public_mark_price"})
                continue
            direction = -1.0 if action == "short" else 1.0
            return_pct = direction * ((price - entry) / entry) * 100.0
            outcome = "favorable" if return_pct > 0 else ("unfavorable" if return_pct < 0 else "flat")
            created = decision.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            record = LearningRecord(
                environment_mode=decision.environment_mode,
                market_type=decision.market_type,
                category="prediction_outcome",
                subject=str(decision.id),
                outcome=outcome,
                lesson="Matured mark-to-market decision evaluation; not a realized trade result.",
                evidence={
                    "decision_id": decision.id,
                    "symbol": decision.symbol,
                    "action": action,
                    "entry_price": entry,
                    "evaluation_price": price,
                    "directional_return_percent": round(return_pct, 6),
                    "horizon_seconds": horizon_seconds,
                    "elapsed_seconds": round((now - created).total_seconds(), 2),
                    "evaluated_at": now.isoformat(),
                    "data_source": "binance_futures_mark_price_read_only" if decision.market_type == "futures" else "binance_spot_public_read_only",
                },
                verified=False,
            )
            db.add(record)
            evaluated.append({
                "decision_id": decision.id,
                "symbol": decision.symbol,
                "action": decision.action,
                "outcome": outcome,
                "directional_return_percent": round(return_pct, 6),
                "evaluation_price": price,
            })
        db.commit()
        return {
            "ok": True,
            "evaluated": len(evaluated),
            "items": evaluated,
            "skipped": skipped,
            "data_source": "binance_public_read_only",
            "note": "This is a single mark-to-market observation after the requested horizon, not realized PnL, a stop/target simulation, or proof of predictive skill.",
        }

    def list_evaluations(self, db: Session, *, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        rows = db.execute(
            select(LearningRecord).where(LearningRecord.category == "prediction_outcome")
            .order_by(LearningRecord.id.desc()).limit(limit)
        ).scalars().all()
        return [{
            "id": row.id,
            "decision_id": row.subject,
            "outcome": row.outcome,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "evidence": row.evidence or {},
            "verified": False,
        } for row in rows]
