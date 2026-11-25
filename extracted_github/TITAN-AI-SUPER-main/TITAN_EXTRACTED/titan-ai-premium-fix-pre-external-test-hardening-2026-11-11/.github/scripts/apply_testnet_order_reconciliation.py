from pathlib import Path
import sys

root = Path(sys.argv[1])

p = root / "app/integrations/binance_adapter.py"
s = p.read_text()
if '"resolved_non_open"' not in s:
    start = s.index("    def reconcile(")
    end = s.index("\n    # Compatibility methods", start)
    new = r"""    def reconcile(self, local_open_orders: list[dict] | None = None) -> dict[str, Any]:
        remote = self.get_open_orders()
        if isinstance(remote, dict) and remote.get("error"):
            return {"ok": False, "error": "remote_fetch_failed", "details": remote,
                    "action": "halt_new_orders_until_resolved"}
        remote_list = remote if isinstance(remote, list) else []
        local = local_open_orders or []
        local_ids = {
            str(value) for order in local
            for value in (order.get("id"), order.get("orderId"), order.get("clientOrderId"))
            if value is not None
        }
        remote_ids = {
            str(value) for order in remote_list
            for value in (order.get("orderId"), order.get("clientOrderId"))
            if value is not None
        }
        missing_local = [o for o in remote_list
                         if not ({str(o.get("orderId")), str(o.get("clientOrderId"))} & local_ids)]
        resolved_non_open = []
        missing_remote = []
        uncertain = []
        for order in local:
            keys = {str(order.get(k)) for k in ("id", "orderId", "clientOrderId") if order.get(k) is not None}
            if keys & remote_ids:
                continue
            client_id = order.get("clientOrderId") or order.get("id")
            symbol = order.get("symbol")
            if not client_id or not symbol:
                uncertain.append(order)
                continue
            lookup = self.get_order_by_client_id(str(symbol), str(client_id))
            if lookup.get("found"):
                remote_order = lookup.get("order") or {}
                status = str(remote_order.get("status", "UNKNOWN")).upper()
                if status in {"FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}:
                    resolved_non_open.append(remote_order)
                else:
                    uncertain.append({"local": order, "remote": remote_order})
            elif lookup.get("confirmed_absent"):
                missing_remote.append(order)
            else:
                uncertain.append({"local": order, "lookup": lookup})
        ok = not missing_local and not missing_remote and not uncertain
        return {
            "ok": ok, "remote_open_orders_count": len(remote_list), "local_open_orders_count": len(local),
            "missing_in_local": len(missing_local), "missing_in_remote": len(missing_remote),
            "resolved_non_open": len(resolved_non_open), "uncertain": len(uncertain),
            "action": "ok" if ok else "review_required_halt_new_orders",
            "details": {"missing_in_local_sample": missing_local[:3], "missing_in_remote_sample": missing_remote[:3],
                        "resolved_non_open_sample": resolved_non_open[:3], "uncertain_sample": uncertain[:3]},
        }
"""
    s = s[:start] + new + s[end:]
    p.write_text(s)

p = root / "app/star_app.py"
s = p.read_text()
if '"binance_testnet_reconciliation_blocked"' not in s:
    old = '''        if state.emergency_stop:
            return J({"ok": False, "error": "emergency_stop_active", "new_orders_blocked": True}, 423)
        return None
'''
    new = '''        if state.emergency_stop:
            return J({"ok": False, "error": "emergency_stop_active", "new_orders_blocked": True}, 423)
        if (state.status_summary or {}).get("binance_testnet_reconciliation_blocked"):
            return J({"ok": False, "error": "testnet_reconciliation_required", "new_orders_blocked": True}, 423)
        return None
'''
    if old not in s:
        raise SystemExit("Testnet emergency-stop reconciliation flag marker missing.")
    s = s.replace(old, new, 1)

if "def _map_testnet_order_status(" not in s:
    start = s.index("async def binance_testnet_order(request):")
    end = s.index("\n\n\nasync def binance_testnet_account", start)
    new = r"""def _map_testnet_order_status(result: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    response = result.get("response") if isinstance(result.get("response"), dict) else {}
    if result.get("order_status_uncertain") or result.get("error") == "order_status_uncertain_blocked":
        return "uncertain", response
    exchange_status = str(response.get("status", "")).upper()
    status_map = {"NEW": "submitted", "PARTIALLY_FILLED": "partially_filled", "FILLED": "filled",
                  "CANCELED": "cancelled", "CANCELLED": "cancelled", "REJECTED": "rejected", "EXPIRED": "cancelled"}
    if exchange_status in status_map:
        return status_map[exchange_status], response
    if result.get("sent_to_exchange") or result.get("duplicate"):
        return "submitted", response
    return ("rejected" if result.get("error") in {"pretrade_blocked", "testnet_adapter_disabled", "testnet_credentials_required"} else "failed"), response


async def binance_testnet_order(request):
    db = db_sess()
    order = None
    try:
        user, err = require_user(request, db)
        if err: return err
        state = db.get(SystemState, 1)
        if state is None:
            return J({"ok": False, "error": "system_state_unavailable", "new_orders_blocked": True}, 503)
        if state.emergency_stop:
            return J({"ok": False, "error": "emergency_stop_active", "new_orders_blocked": True}, 423)
        if (state.status_summary or {}).get("binance_testnet_reconciliation_blocked"):
            return J({"ok": False, "error": "testnet_reconciliation_required", "new_orders_blocked": True}, 423)

        payload = await body(request)
        market = str(payload.get("market", "spot")).lower()
        if market not in {"spot", "futures"}:
            return J({"ok": False, "error": "unsupported_testnet_market"}, 400)
        symbol = str(payload.get("symbol", "BTCUSDT")).strip().upper()
        side = str(payload.get("side", "BUY")).strip().upper()
        order_type = str(payload.get("order_type", "MARKET")).strip().upper()
        quantity = float(payload.get("quantity", 0))
        price = float(payload["price"]) if payload.get("price") is not None else None
        idempotency_key = str(payload.get("idempotency_key", "")).strip()
        if not idempotency_key:
            return J({"ok": False, "error": "valid_idempotency_key_required"}, 400)

        order = db.execute(select(OrderRecord).where(OrderRecord.idempotency_key == idempotency_key)).scalar_one_or_none()
        if order is not None:
            if order.shadow:
                return J({"ok": False, "error": "idempotency_key_reused_for_non_testnet_order"}, 409)
            same_order = (
                order.market_type == market
                and order.symbol.replace("/", "").upper() == symbol.replace("/", "").upper()
                and order.side.lower() == side.lower()
                and order.order_type.lower() == order_type.lower()
                and abs(float(order.quantity) - quantity) <= 1e-12
                and ((order.price is None and price is None) or
                     (order.price is not None and price is not None and abs(float(order.price) - price) <= 1e-12))
            )
            if not same_order:
                return J({"ok": False, "error": "idempotency_key_reused_for_different_order"}, 409)
            if order.status in {"filled", "cancelled", "rejected", "failed"}:
                return J({"ok": True, "duplicate": True, "sent_to_exchange": False,
                          "local_order_id": order.id, "local_status": order.status})

        unresolved = db.execute(select(OrderRecord).where(
            OrderRecord.shadow.is_(False), OrderRecord.market_type == market,
            OrderRecord.status.in_(["pending", "uncertain"]),
        )).scalars().all()
        if any(o.idempotency_key != idempotency_key for o in unresolved):
            return J({"ok": False, "error": "unresolved_testnet_order_status", "new_orders_blocked": True,
                      "unresolved_order_ids": [o.id for o in unresolved if o.idempotency_key != idempotency_key]}, 423)

        if order is None:
            order = OrderRecord(environment_mode="demo", market_type=market, symbol=symbol, side=side.lower(),
                                order_type=order_type.lower(), quantity=quantity, price=price, status="pending",
                                shadow=False, idempotency_key=idempotency_key, client_order_id=idempotency_key,
                                reason="Testnet submission prepared; remote state must be reconciled by client order ID.",
                                metadata_json={"testnet": True, "adapter": "binance_testnet"})
            db.add(order)
            db.commit()
            db.refresh(order)

        adapter = _get_binance_adapter(market)
        try:
            result = adapter.place_order(symbol=symbol, side=side, order_type=order_type, quantity=quantity,
                                         price=price, reference_price=payload.get("reference_price"),
                                         idempotency_key=idempotency_key)
        except Exception as exc:
            order.status = "uncertain"
            order.reason = "Adapter raised after submission preparation; reconcile before any retry."
            order.metadata_json = {**(order.metadata_json or {}), "adapter_exception": type(exc).__name__}
            db.add(order); db.commit()
            return J({"ok": False, "error": "testnet_submission_uncertain", "new_orders_blocked": True,
                      "local_order_id": order.id}, 503)

        local_status, response = _map_testnet_order_status(result)
        order.status = local_status
        order.exchange_order_id = str(response.get("orderId")) if response.get("orderId") is not None else order.exchange_order_id
        order.client_order_id = str(response.get("clientOrderId") or idempotency_key)
        try:
            order.filled_quantity = float(response.get("executedQty", order.filled_quantity or 0) or 0)
            avg_price = response.get("avgPrice") or response.get("price")
            if avg_price not in (None, "", "0", 0): order.avg_fill_price = float(avg_price)
        except (TypeError, ValueError):
            pass
        order.reason = str(result.get("error") or response.get("status") or "Testnet adapter result recorded")[:500]
        order.metadata_json = {**(order.metadata_json or {}), "last_adapter_result": result}
        db.add(order); db.commit(); db.refresh(order)
        return J({**result, "local_order_id": order.id, "local_status": order.status})
    except Exception as exc:
        if order is not None:
            try:
                order.status = "uncertain"
                order.reason = "Unhandled route exception; remote state must be reconciled before retry."
                db.add(order); db.commit()
            except Exception:
                db.rollback()
        return J({"ok": False, "error": str(exc), "new_orders_blocked": bool(order)}, 400)
    finally:
        db.close()
"""
    s = s[:start] + new + s[end:]

start = s.index("async def binance_testnet_reconcile(request):")
end = s.index("\n\n\nasync def binance_testnet_orders", start)
new = r"""async def binance_testnet_reconcile(request):
    db = db_sess()
    try:
        user, err = require_user(request, db)
        if err: return err
        market = (request.query_params.get("market") or "spot").lower()
        if market not in {"spot", "futures"}:
            return J({"ok": False, "error": "unsupported_testnet_market"}, 400)
        adapter = _get_binance_adapter(market)
        local_rows = db.execute(select(OrderRecord).where(
            OrderRecord.shadow.is_(False), OrderRecord.market_type == market,
            OrderRecord.status.in_(["pending", "submitted", "partially_filled", "uncertain"]),
        )).scalars().all()
        local_orders = [{"id": o.idempotency_key, "clientOrderId": o.client_order_id,
                         "orderId": o.exchange_order_id, "symbol": o.symbol} for o in local_rows]
        result = adapter.reconcile(local_open_orders=local_orders)
        state = db.get(SystemState, 1)
        if state is not None:
            summary = dict(state.status_summary or {})
            if result.get("ok"):
                summary.pop("binance_testnet_reconciliation_blocked", None)
                summary.pop("binance_testnet_reconciliation_reason", None)
            else:
                summary["binance_testnet_reconciliation_blocked"] = True
                summary["binance_testnet_reconciliation_reason"] = result.get("action") or result.get("error")
            state.status_summary = summary
            db.add(state)
            db.commit()
        if not result.get("ok"):
            result["new_orders_blocked"] = True
        return J(result)
    except Exception as exc:
        return J({"ok": False, "error": str(exc), "new_orders_blocked": True}, 400)
    finally:
        db.close()
"""
s = s[:start] + new + s[end:]
p.write_text(s)

(root / "tests/test_testnet_order_persistence.py").write_text(r'''import json
from types import SimpleNamespace
import asyncio

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import star_app
from app.models.db import Base
from app.models.entities import OrderRecord, SystemState


class _Request:
    def __init__(self, payload=None, query=None):
        self._payload = payload or {}
        self.query_params = query or {}
        self.headers = {}
        self.cookies = {}

    async def body(self):
        return json.dumps(self._payload).encode()


def _setup(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'testnet-orders.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with Session() as db:
        db.add(SystemState(id=1, emergency_stop=False, status_summary={}))
        db.commit()
    monkeypatch.setattr(star_app, "db_sess", lambda: Session())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (SimpleNamespace(is_admin=True), None))
    return Session


class _FakeAdapter:
    def __init__(self, result):
        self.result = result
        self.calls = 0
        self.local_orders = None
        self.reconcile_result = {"ok": True, "action": "ok"}

    def place_order(self, **kwargs):
        self.calls += 1
        return self.result

    def reconcile(self, local_open_orders=None):
        self.local_orders = local_open_orders
        return dict(self.reconcile_result)


def _response(response):
    return json.loads(response.body.decode())


def test_testnet_order_is_persisted_and_duplicate_key_does_not_create_second_row(tmp_path, monkeypatch):
    Session = _setup(tmp_path, monkeypatch)
    adapter = _FakeAdapter({"ok": True, "sent_to_exchange": True,
        "response": {"orderId": 123, "clientOrderId": "persist-order-0001", "status": "NEW", "executedQty": "0"}})
    monkeypatch.setattr(star_app, "_get_binance_adapter", lambda market=None: adapter)
    payload = {"market": "spot", "symbol": "BTCUSDT", "side": "BUY", "order_type": "LIMIT",
               "quantity": 0.001, "price": 50000, "idempotency_key": "persist-order-0001"}
    first = _response(asyncio.run(star_app.binance_testnet_order(_Request(payload))))
    assert first["ok"] is True and first["local_status"] == "submitted"
    with Session() as db:
        rows = db.execute(select(OrderRecord)).scalars().all()
        assert len(rows) == 1 and rows[0].shadow is False
        assert rows[0].exchange_order_id == "123"
        assert rows[0].client_order_id == "persist-order-0001"
    adapter.result = {"ok": True, "duplicate": True, "sent_to_exchange": False,
        "response": {"orderId": 123, "clientOrderId": "persist-order-0001", "status": "NEW"}}
    second = _response(asyncio.run(star_app.binance_testnet_order(_Request(payload))))
    assert second["duplicate"] is True
    with Session() as db:
        assert len(db.execute(select(OrderRecord)).scalars().all()) == 1
    assert adapter.calls == 2


def test_uncertain_testnet_order_blocks_different_new_orders(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    adapter = _FakeAdapter({"ok": False, "sent_to_exchange": False,
        "order_status_uncertain": True, "error": "order_status_uncertain_blocked"})
    monkeypatch.setattr(star_app, "_get_binance_adapter", lambda market=None: adapter)
    first_payload = {"market": "spot", "symbol": "BTCUSDT", "side": "BUY", "order_type": "LIMIT",
        "quantity": 0.001, "price": 50000, "idempotency_key": "uncertain-order-0001"}
    first = _response(asyncio.run(star_app.binance_testnet_order(_Request(first_payload))))
    assert first["local_status"] == "uncertain"
    second_payload = {**first_payload, "idempotency_key": "different-order-0002"}
    second = _response(asyncio.run(star_app.binance_testnet_order(_Request(second_payload))))
    assert second["error"] == "unresolved_testnet_order_status"
    assert second["new_orders_blocked"] is True
    assert adapter.calls == 1


def test_reconciliation_uses_persisted_local_orders_and_clears_block_on_success(tmp_path, monkeypatch):
    Session = _setup(tmp_path, monkeypatch)
    with Session() as db:
        state = db.get(SystemState, 1)
        state.status_summary = {"binance_testnet_reconciliation_blocked": True}
        db.add(OrderRecord(environment_mode="demo", market_type="futures", symbol="BTCUSDT", side="buy",
            order_type="market", quantity=0.001, status="submitted", shadow=False,
            idempotency_key="reconcile-order-0001", client_order_id="reconcile-order-0001"))
        db.commit()
    adapter = _FakeAdapter({})
    monkeypatch.setattr(star_app, "_get_binance_adapter", lambda market=None: adapter)
    response = _response(asyncio.run(star_app.binance_testnet_reconcile(_Request(query={"market": "futures"}))))
    assert response["ok"] is True
    assert adapter.local_orders == [{"id": "reconcile-order-0001", "clientOrderId": "reconcile-order-0001",
                                     "orderId": None, "symbol": "BTCUSDT"}]
    with Session() as db:
        assert "binance_testnet_reconciliation_blocked" not in (db.get(SystemState, 1).status_summary or {})


def test_reconciliation_mismatch_blocks_future_testnet_orders(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    adapter = _FakeAdapter({})
    adapter.reconcile_result = {"ok": False, "action": "review_required_halt_new_orders"}
    monkeypatch.setattr(star_app, "_get_binance_adapter", lambda market=None: adapter)
    response = _response(asyncio.run(star_app.binance_testnet_reconcile(_Request(query={"market": "spot"}))))
    assert response["new_orders_blocked"] is True
    blocked = star_app._binance_emergency_stop_error()
    assert blocked.status_code == 423
    assert _response(blocked)["error"] == "testnet_reconciliation_required"


def test_idempotency_key_cannot_be_reused_with_different_order_parameters(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    adapter = _FakeAdapter({"ok": True, "sent_to_exchange": True,
        "response": {"orderId": 321, "clientOrderId": "fixed-order-0001", "status": "NEW"}})
    monkeypatch.setattr(star_app, "_get_binance_adapter", lambda market=None: adapter)
    payload = {"market": "spot", "symbol": "BTCUSDT", "side": "BUY", "order_type": "LIMIT",
        "quantity": 0.001, "price": 50000, "idempotency_key": "fixed-order-0001"}
    assert _response(asyncio.run(star_app.binance_testnet_order(_Request(payload))))["ok"] is True
    changed = {**payload, "quantity": 0.002}
    second = _response(asyncio.run(star_app.binance_testnet_order(_Request(changed))))
    assert second["error"] == "idempotency_key_reused_for_different_order"
    assert adapter.calls == 1


def test_shadow_order_idempotency_key_cannot_be_reused_for_testnet(tmp_path, monkeypatch):
    Session = _setup(tmp_path, monkeypatch)
    with Session() as db:
        db.add(OrderRecord(environment_mode="demo", market_type="spot", symbol="BTCUSDT", side="buy",
            order_type="limit", quantity=0.001, status="filled", shadow=True,
            idempotency_key="shadow-key-reuse-01"))
        db.commit()
    adapter = _FakeAdapter({"ok": True, "sent_to_exchange": True, "response": {"orderId": 999, "status": "NEW"}})
    monkeypatch.setattr(star_app, "_get_binance_adapter", lambda market=None: adapter)
    payload = {"market": "spot", "symbol": "BTCUSDT", "side": "BUY", "order_type": "LIMIT",
        "quantity": 0.001, "price": 50000, "idempotency_key": "shadow-key-reuse-01"}
    response = _response(asyncio.run(star_app.binance_testnet_order(_Request(payload))))
    assert response["error"] == "idempotency_key_reused_for_non_testnet_order"
    assert adapter.calls == 0
''')
