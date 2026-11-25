from pathlib import Path
import sys

root = Path(sys.argv[1])
p = root / "app/services/orchestrator.py"
s = p.read_text()

if '"execution_mode": "shadow_simulation"' not in s:
    start = s.index("        # Stale data guard")
    end = s.index('        action = analysis.get("action", "wait")', start)
    analysis_block = '''        # The core analysis path owns data validation and provenance. Do not fetch
        # the same five timeframes twice or silently continue after feed failure.
        try:
            analysis = core_service.analyze(db, symbol=symbol, market_type=market, environment_mode=environment)
        except Exception as e:
            return {"ok": False, "error": "analysis_failed", "detail": str(e), "executed": False,
                    "execution_mode": "blocked"}
'''
    s = s[:start] + analysis_block + s[end:]

    start = s.index("        # Step 3: Execute only if explicitly requested")
    end = s.index("\n        return result", start)
    execution_block = '''        # Step 3: Shadow simulation only. No external Testnet/Live order is sent here.
        if action in ("buy", "long", "short", "sell") and execute_testnet and environment != "live":
            reference_price = analysis.get("entry_price")
            decision_id = analysis.get("decision_id")
            if market == "spot" and action == "sell":
                result["execution_mode"] = "blocked_spot_sell_requires_inventory_sync"
                result["execution_result"] = {"ok": False, "reason": "spot_sell_requires_verified_inventory"}
            elif not reference_price or float(reference_price) <= 0 or not decision_id:
                result["execution_mode"] = "blocked_missing_reference_price_or_decision_id"
                result["execution_result"] = {"ok": False, "reason": "verified_entry_price_and_decision_id_required"}
            else:
                hard_cap = 80.0 if market == "spot" else 800.0 if market == "futures" else 0.0
                quantity = min(0.001, (hard_cap * 0.95) / float(reference_price)) if hard_cap > 0 else 0.0
                shadow_res = self.shadow.create_shadow_order(
                    db, env=environment, market=market, symbol=symbol,
                    side="buy" if action in ("buy", "long") else "sell",
                    order_type="market", quantity=quantity, reference_price=float(reference_price),
                    idempotency_key=f"cycle-{decision_id}", simulate="fill"
                )
                order_status = getattr(shadow_res.order, "status", None)
                did_fill = bool(shadow_res.ok and order_status in {"filled", "partially_filled"})
                result["executed"] = did_fill
                result["execution_mode"] = "shadow_simulation" if did_fill else ("shadow_not_filled" if shadow_res.ok else "blocked")
                result["execution_result"] = {
                    "ok": shadow_res.ok, "message": shadow_res.message,
                    "status": order_status, "order_id": getattr(shadow_res.order, "id", None),
                    "details": shadow_res.details,
                }
'''
    s = s[:start] + execution_block + s[end:]
p.write_text(s)

TESTS = r'''from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.db import Base
from app.models.entities import SystemState, OrderRecord
from app.services.core_service import core_service
from app.services.orchestrator import TradingOrchestrator


def _db(tmp_path, emergency_stop=False):
    engine = create_engine(f"sqlite:///{tmp_path / 'orchestrator.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add(SystemState(id=1, emergency_stop=emergency_stop))
    db.commit()
    return db


def _analysis(action="buy", price=50000.0, decision_id=11):
    return {"action": action, "entry_price": price, "decision_id": decision_id}


def test_orchestrator_reports_shadow_fill_only_after_real_fill(monkeypatch, tmp_path):
    db = _db(tmp_path)
    monkeypatch.setattr(core_service.market_data, "get_multi_timeframe", lambda *a, **k: {})
    monkeypatch.setattr(core_service, "analyze", lambda *a, **k: _analysis())
    try:
        result = TradingOrchestrator().run_cycle(db, symbol="BTC/USDT", market="spot", environment="demo", execute_testnet=True)
        assert result["executed"] is True
        assert result["execution_mode"] == "shadow_simulation"
        assert result["execution_result"]["status"] == "filled"
        order = db.query(OrderRecord).one()
        assert order.quantity * order.avg_fill_price <= 80.0
        assert order.idempotency_key == "cycle-11"
    finally:
        db.close()


def test_orchestrator_does_not_claim_execution_when_emergency_stop_blocks(monkeypatch, tmp_path):
    db = _db(tmp_path, emergency_stop=True)
    monkeypatch.setattr(core_service.market_data, "get_multi_timeframe", lambda *a, **k: {})
    monkeypatch.setattr(core_service, "analyze", lambda *a, **k: _analysis())
    try:
        result = TradingOrchestrator().run_cycle(db, symbol="BTC/USDT", market="spot", environment="demo", execute_testnet=True)
        assert result["executed"] is False
        assert result["execution_mode"] == "blocked"
        assert db.query(OrderRecord).count() == 0
    finally:
        db.close()


def test_orchestrator_blocks_spot_sell_without_inventory_sync(monkeypatch, tmp_path):
    db = _db(tmp_path)
    monkeypatch.setattr(core_service.market_data, "get_multi_timeframe", lambda *a, **k: {})
    monkeypatch.setattr(core_service, "analyze", lambda *a, **k: _analysis(action="sell"))
    try:
        result = TradingOrchestrator().run_cycle(db, symbol="BTC/USDT", market="spot", environment="demo", execute_testnet=True)
        assert result["executed"] is False
        assert result["execution_mode"] == "blocked_spot_sell_requires_inventory_sync"
        assert db.query(OrderRecord).count() == 0
    finally:
        db.close()


def test_orchestrator_blocks_missing_reference_price(monkeypatch, tmp_path):
    db = _db(tmp_path)
    monkeypatch.setattr(core_service.market_data, "get_multi_timeframe", lambda *a, **k: {})
    monkeypatch.setattr(core_service, "analyze", lambda *a, **k: _analysis(price=None))
    try:
        result = TradingOrchestrator().run_cycle(db, symbol="BTC/USDT", market="spot", environment="demo", execute_testnet=True)
        assert result["executed"] is False
        assert result["execution_mode"] == "blocked_missing_reference_price_or_decision_id"
        assert db.query(OrderRecord).count() == 0
    finally:
        db.close()
'''
(root / "tests/test_orchestrator_safety.py").write_text(TESTS)
