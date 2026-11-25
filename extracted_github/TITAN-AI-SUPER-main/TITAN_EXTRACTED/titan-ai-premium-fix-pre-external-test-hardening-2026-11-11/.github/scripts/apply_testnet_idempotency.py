from pathlib import Path
import sys

root = Path(sys.argv[1])
adapter_path = root / "app/integrations/binance_adapter.py"
source = adapter_path.read_text()

if "def get_order_by_client_id(" not in source:
    start = source.index("    def place_order(")
    end = source.index("\n    def close_position(", start)
    replacement = r'''    def get_order_by_client_id(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        """Read-only idempotency lookup. Unknown lookup state must never be retried as absent."""
        path = "/fapi/v1/order" if self.config.market == "futures" else "/api/v3/order"
        result = self._request("GET", path, signed=True, params={
            "symbol": symbol.replace("/", "").upper(),
            "origClientOrderId": client_order_id,
        })
        if isinstance(result, dict) and result.get("error"):
            body = result.get("body", "")
            try:
                payload = json.loads(body) if isinstance(body, str) else body
            except Exception:
                payload = {}
            if isinstance(payload, dict) and (payload.get("code") == -2013 or "does not exist" in str(payload.get("msg", "")).lower()):
                return {"found": False, "confirmed_absent": True}
            return {"found": False, "confirmed_absent": False, "error": "order_lookup_uncertain", "details": result}
        return {"found": True, "confirmed_absent": False, "order": result}

    def place_order(self, symbol: str, side: str, order_type: str, quantity: float, price: float | None = None,
                    reference_price: float | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        # External Testnet order submission is opt-in and requires dedicated Testnet credentials.
        if not self.config.enabled:
            return {"ok": False, "testnet": True, "sent_to_exchange": False, "error": "testnet_adapter_disabled"}
        if self.config.environment not in {"testnet", "demo"}:
            return {"ok": False, "testnet": True, "sent_to_exchange": False, "error": "unapproved_environment"}
        if self.config.market not in {"spot", "futures"}:
            return {"ok": False, "testnet": True, "sent_to_exchange": False, "error": "unsupported_market"}
        if not self._api_key() or not self._api_secret():
            return {"ok": False, "testnet": True, "sent_to_exchange": False, "error": "testnet_credentials_required"}
        if not isinstance(idempotency_key, str) or len(idempotency_key) < 8 or len(idempotency_key) > 36 or not all(c.isalnum() or c in "_.-" for c in idempotency_key):
            return {"ok": False, "testnet": True, "sent_to_exchange": False, "error": "valid_idempotency_key_required"}
        lookup = self.get_order_by_client_id(symbol, idempotency_key)
        if lookup.get("found"):
            return {"ok": True, "testnet": True, "sent_to_exchange": False, "duplicate": True, "response": lookup.get("order")}
        if not lookup.get("confirmed_absent"):
            return {"ok": False, "testnet": True, "sent_to_exchange": False, "error": "order_status_uncertain_blocked", "details": lookup}
        pre = self.gateway.validate(
            symbol=symbol, market=MarketType(self.config.market),
            side=side, order_type=order_type, quantity=quantity, price=price,
            reference_price=reference_price, idempotency_key=idempotency_key,
            environment_mode="demo", live_enabled=False, bypass_attempt=False
        )
        if not pre.ok:
            return {"ok": False, "error": "pretrade_blocked", "details": pre.to_dict(), "sent_to_exchange": False}
        params = {
            "symbol": symbol.replace("/", "").upper(),
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": quantity,
            "newClientOrderId": idempotency_key,
        }
        if price and order_type.upper() in ("LIMIT",):
            params["price"] = price
            params["timeInForce"] = "GTC"
        path = "/fapi/v1/order" if self.config.market == "futures" else "/api/v3/order"
        res = self._request("POST", path, signed=True, params=params)
        return {"ok": "error" not in res, "testnet": True, "response": res, "precheck": pre.to_dict(),
                "sent_to_exchange": "error" not in res, "order_status_uncertain": "error" in res,
                "client_order_id": idempotency_key}
'''
    source = source[:start] + replacement + source[end:]

start = source.index("    def close_position(")
end = source.index("\n    def get_account(", start)
close_replacement = '''    def close_position(self, symbol: str, side: str, quantity: float, *, reference_price: float | None = None,
                       idempotency_key: str | None = None) -> dict[str, Any]:
        if self.config.market == "futures":
            return {"ok": False, "sent_to_exchange": False,
                    "error": "futures_close_disabled_until_reduce_only_position_sync_is_verified"}
        return {"ok": False, "sent_to_exchange": False,
                "error": "spot_close_disabled_until_free_balance_sync_is_verified"}
'''
source = source[:start] + close_replacement + source[end:]
adapter_path.write_text(source)

# Pass caller-provided idempotency keys and reference prices through the authenticated API handlers.
p = root / "app/star_app.py"
s = p.read_text()
start = s.index("async def binance_testnet_order(request):")
end = s.index("\n\nasync def binance_testnet_account", start)
block = s[start:end]
old = """            price=p.get('price')
        )
"""
new = """            price=p.get('price'),
            reference_price=p.get('reference_price'),
            idempotency_key=p.get('idempotency_key')
        )
"""
if new not in block:
    if old not in block:
        raise SystemExit("Testnet order route parameter marker missing")
    block = block.replace(old, new, 1)
s = s[:start] + block + s[end:]

start = s.index("async def binance_testnet_close(request):")
end = s.index("\nasync def binance_testnet_save", start)
block = s[start:end]
old = """            quantity=float(p.get('quantity',0))
        )
"""
new = """            quantity=float(p.get('quantity',0)),
            reference_price=p.get('reference_price'),
            idempotency_key=p.get('idempotency_key')
        )
"""
if new not in block:
    if old not in block:
        raise SystemExit("Testnet close route parameter marker missing")
    block = block.replace(old, new, 1)
s = s[:start] + block + s[end:]
p.write_text(s)

tests = r'''from app.integrations.binance_adapter import BinanceConfig, BinanceTestnetAdapter


def _adapter(monkeypatch):
    monkeypatch.setenv("TITAN_TEST_API_KEY", "test-key")
    monkeypatch.setenv("TITAN_TEST_API_SECRET", "test-secret")
    return BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="testnet", market="spot",
        api_key_ref="env:TITAN_TEST_API_KEY",
        api_secret_ref="env:TITAN_TEST_API_SECRET",
    ))


def test_testnet_order_duplicate_lookup_prevents_second_submission(monkeypatch):
    adapter = _adapter(monkeypatch)
    calls = []

    def fake_request(method, path, signed=False, params=None):
        calls.append(method)
        if method == "GET":
            return {"orderId": 99, "clientOrderId": "titan-order-0001", "status": "NEW"}
        return {"orderId": 100}

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.place_order("BTC/USDT", "BUY", "LIMIT", 0.001, price=50000,
                                 idempotency_key="titan-order-0001")
    assert result["duplicate"] is True
    assert calls == ["GET"]


def test_testnet_order_uncertain_lookup_blocks_submission(monkeypatch):
    adapter = _adapter(monkeypatch)
    calls = []

    def fake_request(method, path, signed=False, params=None):
        calls.append(method)
        return {"error": "http_error", "status": 500, "body": "unknown"}

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.place_order("BTC/USDT", "BUY", "LIMIT", 0.001, price=50000,
                                 idempotency_key="titan-order-0002")
    assert result["error"] == "order_status_uncertain_blocked"
    assert calls == ["GET"]


def test_testnet_order_confirmed_absent_then_submits_once(monkeypatch):
    adapter = _adapter(monkeypatch)
    calls = []

    def fake_request(method, path, signed=False, params=None):
        calls.append((method, params))
        if method == "GET":
            return {"error": "http_error", "status": 400, "body": '{"code":-2013,"msg":"Order does not exist."}'}
        return {"orderId": 100}

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.place_order("BTC/USDT", "BUY", "LIMIT", 0.001, price=50000,
                                 idempotency_key="titan-order-0003")
    assert result["ok"] is True
    assert calls[1][0] == "POST"
    assert calls[1][1]["newClientOrderId"] == "titan-order-0003"


def test_futures_close_is_blocked_until_reduce_only_position_sync_is_verified():
    adapter = BinanceTestnetAdapter(BinanceConfig(enabled=True, environment="testnet", market="futures"))
    result = adapter.close_position("BTC/USDT", "LONG", 0.001, reference_price=50000,
                                    idempotency_key="close-order-0001")
    assert result["ok"] is False
    assert result["sent_to_exchange"] is False
    assert "reduce_only" in result["error"]


def test_binance_order_guard_fails_closed_when_system_state_is_missing(monkeypatch):
    from app import star_app
    class DummyDB:
        def get(self, model, key):
            return None
        def close(self):
            pass
    monkeypatch.setattr(star_app, "db_sess", lambda: DummyDB())
    result = star_app._binance_emergency_stop_error()
    assert result.status_code == 503
'''
(root / "tests/test_testnet_idempotency.py").write_text(tests)
