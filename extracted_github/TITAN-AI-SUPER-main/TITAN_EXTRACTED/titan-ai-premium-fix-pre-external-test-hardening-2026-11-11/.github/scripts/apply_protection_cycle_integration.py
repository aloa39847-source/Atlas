from pathlib import Path
import sys

root = Path(sys.argv[1])

p = root / "app/services/position_management.py"
s = p.read_text()
s = s.replace(
    "    def evaluate_prices(self, db: Session, *, prices: dict[str, float]) -> list[dict[str, Any]]:\n",
    "    def evaluate_prices(self, db: Session, *, prices: dict[str, float], environment_mode: str | None = None, market_type: str | None = None) -> list[dict[str, Any]]:\n",
    1,
)
old = '''        positions = db.execute(select(PositionRecord).where(
            PositionRecord.status == "open", PositionRecord.shadow.is_(True)
        )).scalars().all()
'''
new = '''        query = select(PositionRecord).where(
            PositionRecord.status == "open", PositionRecord.shadow.is_(True)
        )
        if environment_mode:
            query = query.where(PositionRecord.environment_mode == environment_mode)
        if market_type:
            query = query.where(PositionRecord.market_type == market_type)
        positions = db.execute(query).scalars().all()
'''
if old in s:
    s = s.replace(old, new, 1)
elif "query = select(PositionRecord).where(" not in s:
    raise SystemExit("Position monitoring query is neither original nor patched.")
p.write_text(s)

p = root / "app/services/shadow_trading.py"
s = p.read_text()
old = '''        pos = db.get(PositionRecord, position_id)
        if not pos or pos.status != "open":
            return {"ok": False, "reason": "position_not_found_or_not_open"}
        notional = abs(pos.quantity * close_price)
'''
new = '''        pos = db.get(PositionRecord, position_id)
        if not pos or pos.status != "open":
            return {"ok": False, "reason": "position_not_found_or_not_open"}
        if not pos.shadow:
            return {"ok": False, "reason": "real_position_close_not_supported_by_shadow_service"}
        try:
            close_price = float(close_price)
            fee_rate = float(fee_rate)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "invalid_close_price_or_fee_rate"}
        if not __import__("math").isfinite(close_price) or close_price <= 0:
            return {"ok": False, "reason": "positive_finite_close_price_required"}
        if not __import__("math").isfinite(fee_rate) or fee_rate < 0 or fee_rate > 0.05:
            return {"ok": False, "reason": "fee_rate_out_of_range"}
        notional = abs(pos.quantity * close_price)
'''
if old in s:
    s = s.replace(old, new, 1)
elif "real_position_close_not_supported_by_shadow_service" not in s:
    raise SystemExit("Shadow close validation is neither original nor patched.")
p.write_text(s)

p = root / "app/services/core_service.py"
s = p.read_text()
old = '''        result["decision_id"] = rec.id
        result["recorded"] = True
        return result
'''
new = '''        result["decision_id"] = rec.id
        result["recorded"] = True
        m5_batch = batches.get(Timeframe.M5)
        m5_analysis = analyses.get(Timeframe.M5.value)
        result["market_price_source"] = m5_batch.source.value if m5_batch else "unknown"
        result["market_prices"] = (
            {symbol: float(m5_analysis.indicators["close"])}
            if m5_analysis and m5_analysis.indicators.get("close") is not None
            else {}
        )
        return result
'''
if old in s:
    s = s.replace(old, new, 1)
elif "market_price_source" not in s:
    raise SystemExit("Core market-price provenance is neither original nor patched.")
p.write_text(s)

p = root / "app/services/orchestrator.py"
s = p.read_text()
if "from app.services.position_management import position_management" not in s:
    s = s.replace(
        "from app.services.shadow_trading import ShadowTradingService\n",
        "from app.services.shadow_trading import ShadowTradingService\nfrom app.services.position_management import position_management\n",
        1,
    )
old = '''        action = analysis.get("action", "wait")
        
        # Step 2: Risk/Compliance already in decision flow
        result = {
'''
new = '''        action = analysis.get("action", "wait")

        # Existing shadow protection rules are evaluated only against explicitly
        # labeled live-public market prices, never synthetic or unknown candles.
        protection_events = []
        price_source = analysis.get("market_price_source")
        prices = analysis.get("market_prices") or {}
        if price_source == "live_public" and isinstance(prices, dict) and prices:
            try:
                triggered = position_management.evaluate_prices(
                    db, prices=prices, environment_mode=environment, market_type=market,
                )
                for event in triggered:
                    closed = self.shadow.close_shadow_position(
                        db, position_id=event["position_id"], close_price=event["price"],
                    )
                    protection_events.append({**event, "close_result": closed})
                if triggered:
                    db.commit()
            except Exception as e:
                db.rollback()
                protection_events.append({
                    "ok": False, "error": "shadow_protection_processing_failed",
                    "detail": str(e), "new_order_sent": False,
                })

        # Step 2: Risk/Compliance already in decision flow
        result = {
'''
if old in s:
    s = s.replace(old, new, 1)
elif "protection_events = []" not in s:
    raise SystemExit("Orchestrator protection hook is neither original nor patched.")
old = '''            "executed": False,
            "execution_mode": "none",
        }
'''
new = '''            "executed": False,
            "execution_mode": "none",
            "protection_events": protection_events,
            "protection_monitoring": {
                "evaluated": bool(price_source == "live_public" and prices),
                "price_source": price_source or "unknown",
                "note": "Shadow-only protection close; synthetic/unknown market prices are never used to trigger closes.",
            },
        }
'''
if old in s:
    s = s.replace(old, new, 1)
elif '"protection_monitoring"' not in s:
    raise SystemExit("Orchestrator protection result is neither original nor patched.")
p.write_text(s)

tests = r'''from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.db import Base
from app.models.entities import PositionRecord
from app.services.position_management import PositionManagementService
from app.services.shadow_trading import ShadowTradingService


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'protection-cycle.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _position(db, *, environment="demo", market="spot", shadow=True):
    row = PositionRecord(
        environment_mode=environment, market_type=market, symbol="BTC/USDT",
        side="long_spot" if market == "spot" else "long", quantity=1.0,
        entry_avg_price=100.0, mark_price=100.0, status="open", shadow=shadow,
        metadata_json={"protection": {"stop_loss": 95.0, "take_profit": 110.0,
                     "trailing_stop_pct": None, "triggered": False}},
    )
    db.add(row)
    db.flush()
    return row


def test_protection_evaluation_is_scoped_to_environment_and_market(tmp_path):
    db = _db(tmp_path)
    try:
        demo_spot = _position(db, environment="demo", market="spot")
        _position(db, environment="demo", market="futures")
        _position(db, environment="live", market="spot")
        events = PositionManagementService().evaluate_prices(
            db, prices={"BTC/USDT": 94}, environment_mode="demo", market_type="spot",
        )
        assert [event["position_id"] for event in events] == [demo_spot.id]
    finally:
        db.close()


def test_shadow_close_refuses_real_position_and_invalid_prices(tmp_path):
    db = _db(tmp_path)
    try:
        real = _position(db, shadow=False)
        shadow = _position(db, shadow=True)
        service = ShadowTradingService()
        assert service.close_shadow_position(db, position_id=real.id, close_price=90)["reason"] == "real_position_close_not_supported_by_shadow_service"
        assert service.close_shadow_position(db, position_id=shadow.id, close_price=float("nan"))["reason"] == "positive_finite_close_price_required"
        assert service.close_shadow_position(db, position_id=shadow.id, close_price=90, fee_rate=0.2)["reason"] == "fee_rate_out_of_range"
        assert shadow.status == "open"
    finally:
        db.close()


def test_triggered_shadow_protection_can_be_closed_once_with_fees(tmp_path):
    db = _db(tmp_path)
    try:
        position = _position(db)
        protection = PositionManagementService()
        events = protection.evaluate_prices(
            db, prices={"BTC/USDT": 94}, environment_mode="demo", market_type="spot",
        )
        assert len(events) == 1
        closed = ShadowTradingService().close_shadow_position(
            db, position_id=position.id, close_price=94, fee_rate=0.001,
        )
        db.commit()
        assert closed["ok"] is True
        assert position.status == "closed" and position.quantity == 0
        assert position.realized_pnl == -6.0
        assert position.fees_total > 0
        assert protection.evaluate_prices(
            db, prices={"BTC/USDT": 90}, environment_mode="demo", market_type="spot",
        ) == []
    finally:
        db.close()
'''
(root / "tests/test_protection_cycle_integration.py").write_text(tests)


p = root / "tests/test_protection_cycle_integration.py"
with p.open("a") as f:
    f.write(r'''

def test_orchestrator_closes_shadow_position_on_live_public_protection_trigger(tmp_path, monkeypatch):
    from app.services import orchestrator as orchestrator_module
    from app.services.orchestrator import TradingOrchestrator

    db = _db(tmp_path)
    try:
        position = _position(db)
        monkeypatch.setattr(
            orchestrator_module.core_service, "analyze",
            lambda *args, **kwargs: {
                "action": "wait", "market_price_source": "live_public",
                "market_prices": {"BTC/USDT": 94.0}, "decision_id": 10,
            },
        )
        result = TradingOrchestrator().run_cycle(
            db, symbol="BTC/USDT", market="spot", environment="demo",
        )
        assert result["protection_monitoring"]["evaluated"] is True
        assert len(result["protection_events"]) == 1
        assert result["protection_events"][0]["close_result"]["ok"] is True
        assert position.status == "closed"
        assert result["executed"] is False
    finally:
        db.close()


def test_orchestrator_never_triggers_protection_from_synthetic_prices(tmp_path, monkeypatch):
    from app.services import orchestrator as orchestrator_module
    from app.services.orchestrator import TradingOrchestrator

    db = _db(tmp_path)
    try:
        position = _position(db)
        monkeypatch.setattr(
            orchestrator_module.core_service, "analyze",
            lambda *args, **kwargs: {
                "action": "wait", "market_price_source": "synthetic_labeled",
                "market_prices": {"BTC/USDT": 94.0}, "decision_id": 11,
            },
        )
        result = TradingOrchestrator().run_cycle(
            db, symbol="BTC/USDT", market="spot", environment="demo",
        )
        assert result["protection_monitoring"]["evaluated"] is False
        assert result["protection_events"] == []
        assert position.status == "open"
    finally:
        db.close()
''')
