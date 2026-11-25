from pathlib import Path
import sys

root = Path(sys.argv[1])

service = r'''"""Persistent shadow-position protection management.

This service calculates SL/TP/trailing-stop triggers only. It never submits
orders; closing a position remains the responsibility of a separately
verified execution path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import PositionRecord


LONG_SIDES = {"long_spot", "long", "buy"}
SHORT_SIDES = {"short", "sell"}


def _positive(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) and result > 0 else None


class PositionManagementService:
    def configure_protection(
        self, db: Session, *, position_id: int,
        stop_loss: float | None = None, take_profit: float | None = None,
        trailing_stop_pct: float | None = None,
    ) -> dict[str, Any]:
        position = db.get(PositionRecord, position_id)
        if position is None or position.status != "open":
            return {"ok": False, "error": "open_position_not_found"}
        if not position.shadow:
            return {"ok": False, "error": "only_shadow_positions_supported"}
        entry = _positive(position.entry_avg_price)
        if entry is None:
            return {"ok": False, "error": "valid_entry_price_required"}
        sl, tp = _positive(stop_loss), _positive(take_profit)
        if stop_loss is not None and sl is None:
            return {"ok": False, "error": "invalid_stop_loss"}
        if take_profit is not None and tp is None:
            return {"ok": False, "error": "invalid_take_profit"}
        side = str(position.side or "").lower()
        if side in LONG_SIDES:
            if sl is not None and sl >= entry:
                return {"ok": False, "error": "long_stop_loss_must_be_below_entry"}
            if tp is not None and tp <= entry:
                return {"ok": False, "error": "long_take_profit_must_be_above_entry"}
        elif side in SHORT_SIDES:
            if sl is not None and sl <= entry:
                return {"ok": False, "error": "short_stop_loss_must_be_above_entry"}
            if tp is not None and tp >= entry:
                return {"ok": False, "error": "short_take_profit_must_be_below_entry"}
        else:
            return {"ok": False, "error": "unsupported_position_side"}
        trail = None
        if trailing_stop_pct is not None:
            try:
                trail = float(trailing_stop_pct)
            except (TypeError, ValueError):
                return {"ok": False, "error": "invalid_trailing_stop_pct"}
            if not isfinite(trail) or trail <= 0 or trail > 0.5:
                return {"ok": False, "error": "trailing_stop_pct_out_of_range"}
        if sl is None and tp is None and trail is None:
            return {"ok": False, "error": "at_least_one_protection_rule_required"}
        metadata = dict(position.metadata_json or {})
        metadata["protection"] = {
            "stop_loss": sl, "take_profit": tp, "trailing_stop_pct": trail,
            "trailing_stop_price": None, "highest_price": entry if side in LONG_SIDES else None,
            "lowest_price": entry if side in SHORT_SIDES else None,
            "triggered": False, "configured_at": datetime.now(timezone.utc).isoformat(),
        }
        position.metadata_json = metadata
        db.add(position)
        db.flush()
        return {
            "ok": True, "position_id": position.id, "protection": metadata["protection"],
            "note": "Protection triggers are calculated locally; no order is submitted automatically.",
        }

    def evaluate_prices(self, db: Session, *, prices: dict[str, float]) -> list[dict[str, Any]]:
        if not isinstance(prices, dict):
            return []
        positions = db.execute(select(PositionRecord).where(
            PositionRecord.status == "open", PositionRecord.shadow.is_(True)
        )).scalars().all()
        signals: list[dict[str, Any]] = []
        for position in positions:
            metadata = dict(position.metadata_json or {})
            protection = dict(metadata.get("protection") or {})
            if not protection or protection.get("triggered"):
                continue
            price = _positive(prices.get(position.symbol))
            entry = _positive(position.entry_avg_price)
            if price is None or entry is None:
                continue
            side = str(position.side or "").lower()
            is_long, is_short = side in LONG_SIDES, side in SHORT_SIDES
            if not (is_long or is_short):
                continue
            position.mark_price = price
            direction = 1.0 if is_long else -1.0
            position.unrealized_pnl = round(direction * (price - entry) * float(position.quantity or 0), 8)
            reason = None
            stop, target = _positive(protection.get("stop_loss")), _positive(protection.get("take_profit"))
            if stop is not None and ((is_long and price <= stop) or (is_short and price >= stop)):
                reason = "stop_loss"
            elif target is not None and ((is_long and price >= target) or (is_short and price <= target)):
                reason = "take_profit"
            trail_pct = protection.get("trailing_stop_pct")
            if reason is None and trail_pct is not None:
                try:
                    pct = float(trail_pct)
                except (TypeError, ValueError):
                    pct = 0.0
                if 0 < pct <= 0.5:
                    if is_long:
                        high = max(_positive(protection.get("highest_price")) or entry, price)
                        trail_price = high * (1.0 - pct)
                        protection["highest_price"] = high
                        protection["trailing_stop_price"] = trail_price
                        if price <= trail_price:
                            reason = "trailing_stop"
                    else:
                        low = min(_positive(protection.get("lowest_price")) or entry, price)
                        trail_price = low * (1.0 + pct)
                        protection["lowest_price"] = low
                        protection["trailing_stop_price"] = trail_price
                        if price >= trail_price:
                            reason = "trailing_stop"
            if reason:
                protection["triggered"] = True
                protection["trigger_reason"] = reason
                protection["triggered_at"] = datetime.now(timezone.utc).isoformat()
                signals.append({
                    "position_id": position.id, "symbol": position.symbol, "side": side,
                    "quantity": position.quantity, "price": price, "reason": reason,
                    "shadow": True, "order_submitted": False,
                })
            metadata["protection"] = protection
            position.metadata_json = metadata
            db.add(position)
        db.flush()
        return signals

    def list_protections(self, db: Session) -> list[dict[str, Any]]:
        rows = db.execute(select(PositionRecord).where(
            PositionRecord.status == "open", PositionRecord.shadow.is_(True)
        ).order_by(PositionRecord.id.desc())).scalars().all()
        result = []
        for row in rows:
            protection = (row.metadata_json or {}).get("protection")
            if protection:
                result.append({
                    "position_id": row.id, "symbol": row.symbol, "side": row.side,
                    "quantity": row.quantity, "entry_avg_price": row.entry_avg_price,
                    "mark_price": row.mark_price, "protection": protection,
                })
        return result


position_management = PositionManagementService()
'''

(root / "app/services/position_management.py").write_text(service)

core_path = root / "app/services/core_service.py"
core = core_path.read_text()
if "from .position_management import position_management" not in core:
    core = core.replace(
        "from .performance import prediction_tracking\n",
        "from .performance import prediction_tracking\nfrom .position_management import position_management\n",
        1,
    )
core = core.replace(
    '("position_management", "Position Management Engine", "not_implemented"),',
    '("position_management", "Position Management Engine", "implemented_partial"),',
)
core_path.write_text(core)

routes_path = root / "app/api/routes.py"
routes = routes_path.read_text()
if "from app.services.position_management import position_management" not in routes:
    routes = routes.replace(
        "from app.services.performance import prediction_tracking, performance_evaluation\n",
        "from app.services.performance import prediction_tracking, performance_evaluation\nfrom app.services.position_management import position_management\n",
        1,
    )
if "class PositionProtectionRequest(BaseModel):" not in routes:
    marker = """class PredictionVerifyRequest(BaseModel):
    verification_notes: str = Field(..., min_length=20, max_length=2000)


"""
    addition = """class PositionProtectionRequest(BaseModel):
    stop_loss: float | None = Field(None, gt=0)
    take_profit: float | None = Field(None, gt=0)
    trailing_stop_pct: float | None = Field(None, gt=0, le=0.5)


"""
    if marker not in routes:
        raise SystemExit("Position protection request model marker missing.")
    routes = routes.replace(marker, marker + addition, 1)
if '@router.get("/positions/protection")' not in routes:
    marker = """@router.get("/learning")
def list_learning(db: Session = Depends(get_db)) -> dict[str, Any]:
"""
    additions = """@router.get("/positions/protection")
def list_position_protections(
    db: Session = Depends(get_db), _user=Depends(get_current_user),
) -> dict[str, Any]:
    return {
        "items": position_management.list_protections(db),
        "note": "Shadow positions only. Protection triggers do not submit or close orders.",
    }


@router.post("/positions/{position_id}/protection")
def configure_position_protection(
    position_id: int, payload: PositionProtectionRequest,
    db: Session = Depends(get_db), _user=Depends(get_current_user),
) -> dict[str, Any]:
    result = position_management.configure_protection(
        db, position_id=position_id, stop_loss=payload.stop_loss,
        take_profit=payload.take_profit, trailing_stop_pct=payload.trailing_stop_pct,
    )
    if not result.get("ok"):
        status = 404 if result.get("error") == "open_position_not_found" else 400
        raise HTTPException(status_code=status, detail=result.get("error"))
    db.commit()
    return result


"""
    if marker not in routes:
        raise SystemExit("Position protection API route insertion marker missing.")
    routes = routes.replace(marker, additions + marker, 1)
routes_path.write_text(routes)

tests = r'''from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.db import Base
from app.models.entities import PositionRecord
from app.services.position_management import PositionManagementService


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'position-management.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _position(db, *, side="long_spot", shadow=True, entry=100.0):
    row = PositionRecord(
        environment_mode="demo", market_type="spot", symbol="BTC/USDT", side=side,
        quantity=1.0, entry_avg_price=entry, mark_price=entry,
        status="open", shadow=shadow, metadata_json={},
    )
    db.add(row)
    db.flush()
    return row


def test_configure_long_position_protection_and_trigger_stop_loss(tmp_path):
    db = _db(tmp_path)
    try:
        row = _position(db)
        service = PositionManagementService()
        configured = service.configure_protection(db, position_id=row.id, stop_loss=95, take_profit=110)
        db.commit()
        assert configured["ok"] is True
        signals = service.evaluate_prices(db, prices={"BTC/USDT": 94})
        db.commit()
        assert len(signals) == 1
        assert signals[0]["reason"] == "stop_loss"
        assert signals[0]["order_submitted"] is False
        assert service.evaluate_prices(db, prices={"BTC/USDT": 93}) == []
    finally:
        db.close()


def test_short_position_trailing_stop_moves_down_and_triggers_on_rebound(tmp_path):
    db = _db(tmp_path)
    try:
        row = _position(db, side="short", entry=100)
        service = PositionManagementService()
        assert service.configure_protection(db, position_id=row.id, trailing_stop_pct=0.05)["ok"] is True
        assert service.evaluate_prices(db, prices={"BTC/USDT": 90}) == []
        signals = service.evaluate_prices(db, prices={"BTC/USDT": 95})
        assert len(signals) == 1
        assert signals[0]["reason"] == "trailing_stop"
    finally:
        db.close()


def test_rejects_invalid_stop_target_direction_and_real_positions(tmp_path):
    db = _db(tmp_path)
    try:
        service = PositionManagementService()
        long = _position(db)
        assert service.configure_protection(db, position_id=long.id, stop_loss=101)["error"] == "long_stop_loss_must_be_below_entry"
        assert service.configure_protection(db, position_id=long.id, take_profit=99)["error"] == "long_take_profit_must_be_above_entry"
        real = _position(db, shadow=False)
        assert service.configure_protection(db, position_id=real.id, stop_loss=95)["error"] == "only_shadow_positions_supported"
    finally:
        db.close()


def test_protection_requires_at_least_one_rule_and_bounds_trailing_pct(tmp_path):
    db = _db(tmp_path)
    try:
        row = _position(db)
        service = PositionManagementService()
        assert service.configure_protection(db, position_id=row.id)["error"] == "at_least_one_protection_rule_required"
        assert service.configure_protection(db, position_id=row.id, trailing_stop_pct=0.8)["error"] == "trailing_stop_pct_out_of_range"
    finally:
        db.close()
'''
(root / "tests/test_position_management.py").write_text(tests)
