from pathlib import Path
import sys

root = Path(sys.argv[1])

p = root / "app/integrations/binance_adapter.py"
s = p.read_text()
if "futures_position_sync_unavailable" not in s:
    start = s.index("    def close_position(")
    end = s.index("\n    def get_account(", start)
    new = r"""    def close_position(self, symbol: str, side: str, quantity: float, *, reference_price: float | None = None,
                       idempotency_key: str | None = None) -> dict[str, Any]:
        # Testnet-only close; sync the remote position/free balance before any reducing order.
        if not self.config.enabled or self.config.environment not in {"testnet", "demo"}:
            return {"ok": False, "testnet": True, "sent_to_exchange": False, "error": "testnet_adapter_disabled"}
        if not self._api_key() or not self._api_secret():
            return {"ok": False, "testnet": True, "sent_to_exchange": False, "error": "testnet_credentials_required"}
        if not isinstance(idempotency_key, str) or len(idempotency_key) < 8 or len(idempotency_key) > 36 or not all(c.isalnum() or c in "_.-" for c in idempotency_key):
            return {"ok": False, "testnet": True, "sent_to_exchange": False, "error": "valid_idempotency_key_required"}
        lookup = self.get_order_by_client_id(symbol, idempotency_key)
        if lookup.get("found"):
            return {"ok": True, "testnet": True, "sent_to_exchange": False, "duplicate": True, "response": lookup.get("order")}
        if not lookup.get("confirmed_absent"):
            return {"ok": False, "testnet": True, "sent_to_exchange": False,
                    "error": "order_status_uncertain_blocked", "order_status_uncertain": True, "details": lookup}
        if not isinstance(quantity, (int, float)) or quantity <= 0:
            return {"ok": False, "testnet": True, "sent_to_exchange": False, "error": "positive_close_quantity_required"}

        normalized_symbol = symbol.replace("/", "").upper()
        requested_side = str(side or "").upper()
        try:
            if self.config.market == "futures":
                account = self.get_account()
                if not isinstance(account, dict) or account.get("error") or not isinstance(account.get("positions"), list):
                    return {"ok": False, "testnet": True, "sent_to_exchange": False,
                            "error": "futures_position_sync_unavailable", "details": account}
                positions = [p for p in account["positions"] if str(p.get("symbol", "")).upper() == normalized_symbol
                             and abs(float(p.get("positionAmt", 0) or 0)) > 0]
                if not positions:
                    return {"ok": False, "testnet": True, "sent_to_exchange": False, "error": "no_open_futures_position_to_close"}
                candidates = []
                for pos in positions:
                    pos_side = str(pos.get("positionSide", "BOTH")).upper()
                    amount = float(pos.get("positionAmt", 0) or 0)
                    is_long = pos_side == "LONG" or (pos_side == "BOTH" and amount > 0)
                    is_short = pos_side == "SHORT" or (pos_side == "BOTH" and amount < 0)
                    if ((requested_side in {"LONG", "SELL"} and is_long) or
                        (requested_side in {"SHORT", "BUY"} and is_short)):
                        candidates.append(pos)
                if len(candidates) != 1:
                    return {"ok": False, "testnet": True, "sent_to_exchange": False,
                            "error": "futures_position_side_ambiguous_or_mismatched"}
                position = candidates[0]
                position_side = str(position.get("positionSide", "BOTH")).upper()
                amount = float(position.get("positionAmt", 0) or 0)
                is_long = position_side == "LONG" or (position_side == "BOTH" and amount > 0)
                expected_side = "SELL" if is_long else "BUY"
                if requested_side in {"BUY", "SELL"} and requested_side != expected_side:
                    return {"ok": False, "testnet": True, "sent_to_exchange": False, "error": "futures_close_side_mismatch"}
                if requested_side in {"LONG", "SHORT"} and requested_side != ("LONG" if is_long else "SHORT"):
                    return {"ok": False, "testnet": True, "sent_to_exchange": False, "error": "futures_close_side_mismatch"}
                available = abs(amount)
                if quantity > available + 1e-12:
                    return {"ok": False, "testnet": True, "sent_to_exchange": False,
                            "error": "close_quantity_exceeds_synced_position", "available_quantity": available}
                # Never trust a caller-supplied price for the hard notional check.
                ref = self.public_data.get_ticker_price(symbol, market="futures") * 1.01
                pre = self.gateway.validate(symbol=symbol, market=MarketType.FUTURES, side=expected_side,
                    order_type="market", quantity=quantity, reference_price=ref, idempotency_key=idempotency_key,
                    environment_mode="demo", live_enabled=False, bypass_attempt=False)
                if not pre.ok:
                    return {"ok": False, "testnet": True, "sent_to_exchange": False,
                            "error": "pretrade_blocked", "details": pre.to_dict()}
                params = {"symbol": normalized_symbol, "side": expected_side, "type": "MARKET",
                          "quantity": quantity, "newClientOrderId": idempotency_key}
                # Hedge Mode uses positionSide; One-way Mode uses reduceOnly.
                if position_side in {"LONG", "SHORT"}:
                    params["positionSide"] = position_side
                else:
                    params["reduceOnly"] = "true"
                try:
                    response = self._request("POST", "/fapi/v1/order", signed=True, params=params)
                except Exception as exc:
                    return {"ok": False, "testnet": True, "sent_to_exchange": False,
                            "error": "order_status_uncertain_blocked", "order_status_uncertain": True,
                            "details": type(exc).__name__}
            elif self.config.market == "spot":
                if requested_side not in {"SELL", "LONG"}:
                    return {"ok": False, "testnet": True, "sent_to_exchange": False, "error": "spot_close_requires_sell_side"}
                info = self.get_exchange_info(symbol)
                if not isinstance(info, dict) or info.get("error") or not info.get("symbols"):
                    return {"ok": False, "testnet": True, "sent_to_exchange": False,
                            "error": "spot_symbol_sync_unavailable", "details": info}
                base_asset = info["symbols"][0].get("baseAsset")
                account = self.get_account()
                if not base_asset or not isinstance(account, dict) or account.get("error") or not isinstance(account.get("balances"), list):
                    return {"ok": False, "testnet": True, "sent_to_exchange": False,
                            "error": "spot_balance_sync_unavailable", "details": account}
                balance = next((b for b in account["balances"] if b.get("asset") == base_asset), None)
                free = float((balance or {}).get("free", 0) or 0)
                if quantity > free + 1e-12:
                    return {"ok": False, "testnet": True, "sent_to_exchange": False,
                            "error": "close_quantity_exceeds_synced_free_balance", "available_quantity": free}
                ref = self.public_data.get_ticker_price(symbol, market="spot") * 1.01
                pre = self.gateway.validate(symbol=symbol, market=MarketType.SPOT, side="sell",
                    order_type="market", quantity=quantity, reference_price=ref, idempotency_key=idempotency_key,
                    environment_mode="demo", live_enabled=False, bypass_attempt=False)
                if not pre.ok:
                    return {"ok": False, "testnet": True, "sent_to_exchange": False,
                            "error": "pretrade_blocked", "details": pre.to_dict()}
                params = {"symbol": normalized_symbol, "side": "SELL", "type": "MARKET",
                          "quantity": quantity, "newClientOrderId": idempotency_key}
                try:
                    response = self._request("POST", "/api/v3/order", signed=True, params=params)
                except Exception as exc:
                    return {"ok": False, "testnet": True, "sent_to_exchange": False,
                            "error": "order_status_uncertain_blocked", "order_status_uncertain": True,
                            "details": type(exc).__name__}
            else:
                return {"ok": False, "testnet": True, "sent_to_exchange": False, "error": "unsupported_market"}
        except Exception as exc:
            return {"ok": False, "testnet": True, "sent_to_exchange": False,
                    "error": "position_or_balance_sync_failed", "details": str(exc)}
        return {"ok": "error" not in response, "testnet": True, "response": response,
                "sent_to_exchange": "error" not in response, "order_status_uncertain": "error" in response,
                "reduce_only_close": True, "client_order_id": idempotency_key}
"""
    s = s[:start] + new + s[end:]
    p.write_text(s)

p = root / "app/star_app.py"
s = p.read_text()
if "reduce_only = bool(payload.get(\"reduce_only\", False))" not in s:
    s = s.replace('''        idempotency_key = str(payload.get("idempotency_key", "")).strip()
        if not idempotency_key:
''','''        idempotency_key = str(payload.get("idempotency_key", "")).strip()
        reduce_only = bool(payload.get("reduce_only", False))
        if not idempotency_key:
''',1)
    s = s.replace('''                and ((order.price is None and price is None) or
                     (order.price is not None and price is not None and abs(float(order.price) - price) <= 1e-12))
            )
''','''                and ((order.price is None and price is None) or
                     (order.price is not None and price is not None and abs(float(order.price) - price) <= 1e-12))
                and bool((order.metadata_json or {}).get("reduce_only", False)) == reduce_only
            )
''',1)
    s = s.replace('''                                reason="Testnet submission prepared; remote state must be reconciled by client order ID.",
                                metadata_json={"testnet": True, "adapter": "binance_testnet"})
''','''                                reason="Testnet submission prepared; remote state must be reconciled by client order ID.",
                                metadata_json={"testnet": True, "adapter": "binance_testnet", "reduce_only": reduce_only})
''',1)
    old = '''            result = adapter.place_order(symbol=symbol, side=side, order_type=order_type, quantity=quantity,
                                         price=price, reference_price=payload.get("reference_price"),
                                         idempotency_key=idempotency_key)
'''
    new = '''            if reduce_only:
                result = adapter.close_position(symbol=symbol, side=side, quantity=quantity,
                                                reference_price=payload.get("reference_price"),
                                                idempotency_key=idempotency_key)
            else:
                result = adapter.place_order(symbol=symbol, side=side, order_type=order_type, quantity=quantity,
                                             price=price, reference_price=payload.get("reference_price"),
                                             idempotency_key=idempotency_key)
'''
    if old not in s:
        raise SystemExit("Testnet order dispatch marker missing.")
    s = s.replace(old, new, 1)

start = s.index("async def binance_testnet_close(request):")
end = s.index("\n\nasync def binance_testnet_save", start)
new = r"""async def binance_testnet_close(request):
    payload = await body(request)
    payload["reduce_only"] = True

    class _CloseRequestProxy:
        def __init__(self, original, data):
            self._original = original
            self._data = data
        def __getattr__(self, name):
            return getattr(self._original, name)
        async def body(self):
            return json.dumps(self._data).encode("utf-8")

    return await binance_testnet_order(_CloseRequestProxy(request, payload))
"""
s = s[:start] + new + s[end:]
p.write_text(s)

# Extend the route-level regression fixture to cover persisted reduce-only close orders.
p = root / "tests/test_testnet_order_persistence.py"
s = p.read_text()
if "def close_position(self, **kwargs):" not in s:
    s = s.replace('''    def place_order(self, **kwargs):
        self.calls += 1
        return self.result

    def reconcile(self, local_open_orders=None):
''','''    def place_order(self, **kwargs):
        self.calls += 1
        return self.result

    def close_position(self, **kwargs):
        self.calls += 1
        return self.result

    def reconcile(self, local_open_orders=None):
''',1)
if "test_testnet_close_uses_reduce_only_adapter_and_persists_same_order_ledger" not in s:
    s += '''

def test_testnet_close_uses_reduce_only_adapter_and_persists_same_order_ledger(tmp_path, monkeypatch):
    Session = _setup(tmp_path, monkeypatch)
    adapter = _FakeAdapter({"ok": True, "sent_to_exchange": True, "reduce_only_close": True,
        "response": {"orderId": 777, "clientOrderId": "reduce-close-0001", "status": "NEW"}})
    monkeypatch.setattr(star_app, "_get_binance_adapter", lambda market=None: adapter)
    payload = {"market": "futures", "symbol": "BTCUSDT", "side": "SELL", "quantity": 0.001,
               "idempotency_key": "reduce-close-0001"}
    response = _response(asyncio.run(star_app.binance_testnet_close(_Request(payload))))
    assert response["ok"] is True
    assert response["local_status"] == "submitted"
    with Session() as db:
        row = db.execute(select(OrderRecord)).scalar_one()
        assert row.shadow is False
        assert row.metadata_json["reduce_only"] is True
        assert row.exchange_order_id == "777"
    assert adapter.calls == 1
'''
    if "import asyncio" not in s:
        s = s.replace("import json\n", "import json\nimport asyncio\n", 1)
    s = s.replace('__import__("asyncio").run(', 'asyncio.run(')
    p.write_text(s)

p = root / "tests/test_testnet_idempotency.py"
s = p.read_text().replace('assert "reduce_only" in result["error"]', 'assert result["error"] == "testnet_credentials_required"', 1)
if "def _futures_close_adapter(" not in s:
    s += '''

def _futures_close_adapter(monkeypatch):
    monkeypatch.setenv("TITAN_TEST_API_KEY", "test-key")
    monkeypatch.setenv("TITAN_TEST_API_SECRET", "test-secret")
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="testnet", market="futures",
        api_key_ref="env:TITAN_TEST_API_KEY", api_secret_ref="env:TITAN_TEST_API_SECRET",
    ))
    monkeypatch.setattr(adapter.public_data, "get_ticker_price", lambda symbol, market: 50000.0)
    return adapter


def test_futures_close_uses_synced_long_position_and_reduce_only(monkeypatch):
    adapter = _futures_close_adapter(monkeypatch)
    calls = []
    def fake_request(method, path, signed=False, params=None):
        calls.append((method, path, params))
        if method == "GET" and path == "/fapi/v1/order":
            return {"error": "http_error", "status": 400, "body": '{"code":-2013,"msg":"Order does not exist."}'}
        if method == "GET" and path == "/fapi/v2/account":
            return {"positions": [{"symbol": "BTCUSDT", "positionAmt": "0.005", "positionSide": "BOTH"}]}
        return {"orderId": 777, "status": "NEW"}
    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.close_position("BTCUSDT", "SELL", 0.005, reference_price=1.0, idempotency_key="futures-close-001")
    assert result["ok"] is True and result["reduce_only_close"] is True
    post = [x for x in calls if x[0] == "POST"][0]
    assert post[1] == "/fapi/v1/order" and post[2]["side"] == "SELL"
    assert post[2]["reduceOnly"] == "true"


def test_futures_close_uses_buy_side_for_synced_short_position(monkeypatch):
    adapter = _futures_close_adapter(monkeypatch)
    calls = []
    def fake_request(method, path, signed=False, params=None):
        calls.append((method, path, params))
        if method == "GET" and path == "/fapi/v1/order":
            return {"error": "http_error", "status": 400, "body": '{"code":-2013,"msg":"Order does not exist."}'}
        if method == "GET" and path == "/fapi/v2/account":
            return {"positions": [{"symbol": "BTCUSDT", "positionAmt": "-0.004", "positionSide": "BOTH"}]}
        return {"orderId": 778, "status": "NEW"}
    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.close_position("BTCUSDT", "BUY", 0.004, idempotency_key="futures-close-002")
    assert result["ok"] is True
    post = [x for x in calls if x[0] == "POST"][0]
    assert post[2]["side"] == "BUY" and post[2]["reduceOnly"] == "true"


def test_futures_close_rejects_quantity_above_synced_position(monkeypatch):
    adapter = _futures_close_adapter(monkeypatch)
    calls = []
    def fake_request(method, path, signed=False, params=None):
        calls.append(method)
        if method == "GET" and path == "/fapi/v1/order":
            return {"error": "http_error", "status": 400, "body": '{"code":-2013,"msg":"Order does not exist."}'}
        if method == "GET" and path == "/fapi/v2/account":
            return {"positions": [{"symbol": "BTCUSDT", "positionAmt": "0.002", "positionSide": "BOTH"}]}
        raise AssertionError("close must not be sent")
    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.close_position("BTCUSDT", "SELL", 0.003, idempotency_key="futures-close-003")
    assert result["error"] == "close_quantity_exceeds_synced_position"
    assert "POST" not in calls


def test_futures_close_fails_closed_when_position_sync_is_unavailable(monkeypatch):
    adapter = _futures_close_adapter(monkeypatch)
    calls = []
    def fake_request(method, path, signed=False, params=None):
        calls.append(method)
        if method == "GET" and path == "/fapi/v1/order":
            return {"error": "http_error", "status": 400, "body": '{"code":-2013,"msg":"Order does not exist."}'}
        return {"error": "http_error", "status": 503, "body": "offline"}
    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.close_position("BTCUSDT", "SELL", 0.001, idempotency_key="futures-close-004")
    assert result["error"] == "futures_position_sync_unavailable"
    assert "POST" not in calls


def test_futures_close_post_timeout_is_marked_uncertain(monkeypatch):
    adapter = _futures_close_adapter(monkeypatch)
    def fake_request(method, path, signed=False, params=None):
        if method == "GET" and path == "/fapi/v1/order":
            return {"error": "http_error", "status": 400, "body": '{"code":-2013,"msg":"Order does not exist."}'}
        if method == "GET" and path == "/fapi/v2/account":
            return {"positions": [{"symbol": "BTCUSDT", "positionAmt": "0.002", "positionSide": "BOTH"}]}
        if method == "POST":
            raise TimeoutError("simulated timeout after request dispatch")
        raise AssertionError("unexpected request")
    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.close_position("BTCUSDT", "SELL", 0.002, idempotency_key="futures-close-timeout")
    assert result["order_status_uncertain"] is True
    assert result["error"] == "order_status_uncertain_blocked"


def test_hedge_mode_futures_close_uses_position_side_without_reduce_only(monkeypatch):
    adapter = _futures_close_adapter(monkeypatch)
    calls = []
    def fake_request(method, path, signed=False, params=None):
        calls.append((method, path, params))
        if method == "GET" and path == "/fapi/v1/order":
            return {"error": "http_error", "status": 400, "body": '{"code":-2013,"msg":"Order does not exist."}'}
        if method == "GET" and path == "/fapi/v2/account":
            return {"positions": [{"symbol": "BTCUSDT", "positionAmt": "0.002", "positionSide": "LONG"}]}
        return {"orderId": 779, "status": "NEW"}
    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.close_position("BTCUSDT", "LONG", 0.002, idempotency_key="futures-close-005")
    assert result["ok"] is True
    post = [x for x in calls if x[0] == "POST"][0]
    assert post[2]["side"] == "SELL" and post[2]["positionSide"] == "LONG"
    assert "reduceOnly" not in post[2]
'''
p.write_text(s)


test_file = root / "tests/test_testnet_idempotency.py"
test_text = test_file.read_text()
if "test_futures_close_post_timeout_is_marked_uncertain" not in test_text:
    test_text += '''

def test_futures_close_post_timeout_is_marked_uncertain(monkeypatch):
    adapter = _futures_close_adapter(monkeypatch)
    def fake_request(method, path, signed=False, params=None):
        if method == "GET" and path == "/fapi/v1/order":
            return {"error": "http_error", "status": 400, "body": '{"code":-2013,"msg":"Order does not exist."}'}
        if method == "GET" and path == "/fapi/v2/account":
            return {"positions": [{"symbol": "BTCUSDT", "positionAmt": "0.002", "positionSide": "BOTH"}]}
        if method == "POST":
            raise TimeoutError("simulated timeout after request dispatch")
        raise AssertionError("unexpected request")
    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.close_position("BTCUSDT", "SELL", 0.002, idempotency_key="futures-close-timeout")
    assert result["order_status_uncertain"] is True
    assert result["error"] == "order_status_uncertain_blocked"
'''
    test_file.write_text(test_text)
