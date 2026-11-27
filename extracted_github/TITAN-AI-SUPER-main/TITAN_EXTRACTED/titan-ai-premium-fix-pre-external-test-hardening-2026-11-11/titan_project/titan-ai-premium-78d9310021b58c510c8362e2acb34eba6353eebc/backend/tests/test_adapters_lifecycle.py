from app.core.enums import MarketType
from app.integrations.binance_adapter import BinanceAdapter, BinanceConfig
from app.integrations.telegram_adapter import TelegramAdapter


def test_binance_shadow_order_does_not_send():
    b = BinanceAdapter(BinanceConfig(enabled=False, environment="demo"))
    r = b.place_order_shadow(symbol="BTC/USDT", market=MarketType.SPOT, side="buy", order_type="limit", quantity=0.01, price=50000, reference_price=50000)
    assert r["mode"] == "shadow"
    assert r["sent_to_exchange"] is False
    assert "precheck" in r


def test_binance_live_blocked_in_foundation():
    b = BinanceAdapter()
    r = b.place_order_live(symbol="BTC/USDT")
    assert r["ok"] is False and r["sent_to_exchange"] is False


def test_telegram_sensitive_command_not_executed():
    t = TelegramAdapter()
    r = t.parse_command("123", "/startbot")
    assert r["sensitive"] is True
    assert r["executed"] is False
    assert r["authorized"] is False


def test_binance_testnet_adapter_rejects_futures_execution():
    from app.integrations.binance_adapter import BinanceTestnetAdapter, BinanceConfig
    adapter = BinanceTestnetAdapter(BinanceConfig(enabled=True, environment="testnet", market="futures"))
    result = adapter.place_order("BTCUSDT", "BUY", "MARKET", 0.001)
    assert result["ok"] is False
    assert result["sent_to_exchange"] is False
    assert result["error"] == "testnet_credentials_missing"

def test_binance_testnet_futures_order_uses_guarded_endpoint_and_leverage(monkeypatch):
    adapter = BinanceAdapter(BinanceConfig(enabled=True, environment="testnet", market="futures"))
    monkeypatch.setattr(adapter, "_api_key", lambda: "testnet-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "testnet-secret")
    calls = []

    def fake_request(method, path, signed=False, params=None):
        calls.append((method, path, dict(params or {})))
        if path == "/fapi/v1/positionSide/dual":
            return {"dualSidePosition": False}
        if path == "/fapi/v1/positionSide/dual":
            return {"dualSidePosition": False}
        if path == "/fapi/v2/account":
            return {"canTrade": True, "availableBalance": "1000"}
        if path == "/fapi/v1/openOrders":
            return []
        if path == "/fapi/v1/ticker/price":
            return {"symbol": "BTCUSDT", "price": "50000"}
        if path == "/fapi/v1/exchangeInfo":
            return {"symbols": [{"symbol": "BTCUSDT", "status": "TRADING", "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                {"filterType": "MIN_NOTIONAL", "notional": "5"}]}]}
        if path == "/fapi/v1/marginType":
            return {}
        if path == "/fapi/v1/leverage":
            return {"symbol": "BTCUSDT", "leverage": 5}
        if path == "/fapi/v1/order":
            return {"orderId": 123, "status": "NEW"}
        raise AssertionError("unexpected endpoint: " + path)

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.place_order("BTC/USDT", "BUY", "MARKET", 0.001, leverage=5)
    assert result["ok"] is True
    assert result["sent_to_exchange"] is True
    assert result["testnet"] is True
    assert result["market"] == "futures"
    assert any(path == "/fapi/v1/marginType" and params.get("marginType") == "ISOLATED" for _, path, params in calls)
    assert any(path == "/fapi/v1/leverage" for _, path, _ in calls)
    assert calls.index(next(call for call in calls if call[1] == "/fapi/v1/marginType")) < calls.index(next(call for call in calls if call[1] == "/fapi/v1/leverage"))
    order_call = next(call for call in calls if call[1] == "/fapi/v1/order")
    assert order_call[2]["quantity"] == "0.001"


def test_binance_testnet_futures_enforces_800_usdt_notional_cap(monkeypatch):
    adapter = BinanceAdapter(BinanceConfig(enabled=True, environment="testnet", market="futures"))
    monkeypatch.setattr(adapter, "_api_key", lambda: "testnet-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "testnet-secret")
    calls = []

    def fake_request(method, path, signed=False, params=None):
        calls.append(path)
        if path == "/fapi/v1/positionSide/dual":
            return {"dualSidePosition": False}
        if path == "/fapi/v1/openOrders":
            return []
        if path == "/fapi/v2/account":
            return {"canTrade": True}
        if path == "/fapi/v1/ticker/price":
            return {"symbol": "BTCUSDT", "price": "50000"}
        if path == "/fapi/v1/exchangeInfo":
            return {"symbols": [{"symbol": "BTCUSDT", "status": "TRADING", "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001"}]}]}
        raise AssertionError("order or leverage must not be sent: " + path)

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.place_order("BTC/USDT", "BUY", "MARKET", 0.02, leverage=5)
    assert result["ok"] is False
    assert result["sent_to_exchange"] is False
    assert result["error"] == "pretrade_blocked"
    assert "notional_exceeds_limit" in result["details"]["reason"]
    assert "/fapi/v1/order" not in calls
    assert "/fapi/v1/leverage" not in calls


def test_binance_testnet_futures_rejects_leverage_above_ten(monkeypatch):
    adapter = BinanceAdapter(BinanceConfig(enabled=True, environment="testnet", market="futures"))
    monkeypatch.setattr(adapter, "_api_key", lambda: "testnet-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "testnet-secret")
    result = adapter.place_order("BTC/USDT", "BUY", "MARKET", 0.001, leverage=11)
    assert result["ok"] is False
    assert result["sent_to_exchange"] is False
    assert result["error"] == "leverage_outside_titan_cap"


def _spot_testnet_adapter_with_mocked_account(monkeypatch, balances):
    adapter = BinanceAdapter(BinanceConfig(enabled=True, environment="testnet", market="spot"))
    monkeypatch.setattr(adapter, "_api_key", lambda: "testnet-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "testnet-secret")

    def fake_request(method, path, signed=False, params=None):
        if path == "/api/v3/account":
            return {"canTrade": True, "balances": balances}
        if path == "/api/v3/openOrders":
            return []
        if path == "/api/v3/ticker/price":
            return {"symbol": "BTCUSDT", "price": "50000"}
        if path == "/api/v3/exchangeInfo":
            return {"symbols": [{"symbol": "BTCUSDT", "status": "TRADING", "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                {"filterType": "MIN_NOTIONAL", "minNotional": "5"}]}]}
        raise AssertionError("unexpected endpoint: " + path)

    monkeypatch.setattr(adapter, "_request", fake_request)
    return adapter


def test_binance_testnet_spot_buy_requires_free_quote_balance(monkeypatch):
    adapter = _spot_testnet_adapter_with_mocked_account(monkeypatch, [
        {"asset": "BTC", "free": "1"},
        {"asset": "USDT", "free": "40"},
    ])
    result = adapter.place_order("BTC/USDT", "BUY", "MARKET", 0.001)
    assert result["ok"] is False
    assert result["sent_to_exchange"] is False
    assert result["error"] == "insufficient_testnet_spot_balance"
    assert result["asset"] == "USDT"
    assert result["required"] > result["available"]


def test_binance_testnet_spot_sell_requires_free_base_balance(monkeypatch):
    adapter = _spot_testnet_adapter_with_mocked_account(monkeypatch, [
        {"asset": "BTC", "free": "0.0005"},
        {"asset": "USDT", "free": "1000"},
    ])
    result = adapter.place_order("BTC/USDT", "SELL", "MARKET", 0.001)
    assert result["ok"] is False
    assert result["sent_to_exchange"] is False
    assert result["error"] == "insufficient_testnet_spot_balance"
    assert result["asset"] == "BTC"


def test_close_position_maps_long_spot_to_sell_and_short_to_buy(monkeypatch):
    from app.integrations.binance_adapter import BinanceTestnetAdapter, BinanceConfig

    spot = BinanceTestnetAdapter(BinanceConfig(enabled=True, environment="testnet", market="spot"))
    futures = BinanceTestnetAdapter(BinanceConfig(enabled=True, environment="testnet", market="futures"))
    captured = []

    def capture(adapter_name):
        def fake_place(symbol, side, order_type, quantity, **kwargs):
            captured.append((adapter_name, symbol, side, order_type, quantity, kwargs))
            return {"ok": True, "sent_to_exchange": False}
        return fake_place

    monkeypatch.setattr(spot, "place_order", capture("spot"))
    monkeypatch.setattr(futures, "place_order", capture("futures"))

    spot.close_position("BTCUSDT", "long_spot", 0.01, idempotency_key="close-spot-long-01")
    futures.close_position("BTCUSDT", "short", 0.01, leverage=2, idempotency_key="close-futures-short-01")

    assert captured[0][2] == "SELL"
    assert captured[0][5]["reduce_only"] is False
    assert captured[1][2] == "BUY"
    assert captured[1][5]["reduce_only"] is True


def test_close_position_rejects_unknown_position_side():
    from app.integrations.binance_adapter import BinanceTestnetAdapter, BinanceConfig

    adapter = BinanceTestnetAdapter(BinanceConfig(enabled=True, environment="testnet", market="spot"))
    result = adapter.close_position("BTCUSDT", "unknown", 0.01)
    assert result["ok"] is False
    assert result["sent_to_exchange"] is False
    assert result["error"] == "invalid_position_side_for_close"


def test_futures_margin_type_accepts_binance_already_isolated_code(monkeypatch):
    adapter = BinanceAdapter(BinanceConfig(enabled=True, environment="testnet", market="futures"))
    monkeypatch.setattr(adapter, "_execution_ready", lambda: None)
    calls = []
    def fake_request(method, path, signed=False, params=None):
        calls.append((method, path, params))
        return {"error": "http_error", "status": 400, "body": '{"code":-4046,"msg":"No need to change margin type."}'}
    monkeypatch.setattr(adapter, "_request", fake_request)

    result = adapter.set_margin_type("BTC/USDT", "ISOLATED")

    assert result["ok"] is True
    assert result["already_isolated"] is True
    assert calls[0][1] == "/fapi/v1/marginType"
    assert calls[0][2]["marginType"] == "ISOLATED"


def test_futures_margin_type_fails_closed_when_exchange_refuses_isolated(monkeypatch):
    adapter = BinanceAdapter(BinanceConfig(enabled=True, environment="testnet", market="futures"))
    monkeypatch.setattr(adapter, "_execution_ready", lambda: None)
    monkeypatch.setattr(adapter, "_request", lambda *args, **kwargs: {
        "error": "http_error", "status": 400, "body": '{"code":-4047,"msg":"Position exists."}'
    })

    result = adapter.set_margin_type("BTCUSDT", "ISOLATED")

    assert result["ok"] is False
    assert result["sent_to_exchange"] is False
    assert result["error"] == "isolated_margin_setup_failed"


def test_futures_margin_type_cannot_be_changed_to_cross(monkeypatch):
    adapter = BinanceAdapter(BinanceConfig(enabled=True, environment="testnet", market="futures"))
    monkeypatch.setattr(adapter, "_execution_ready", lambda: None)

    result = adapter.set_margin_type("BTCUSDT", "CROSSED")

    assert result["ok"] is False
    assert result["sent_to_exchange"] is False
    assert result["error"] == "titan_futures_margin_mode_must_be_isolated"


def test_futures_reduce_only_close_skips_margin_and_leverage_setup(monkeypatch):
    from types import SimpleNamespace

    adapter = BinanceAdapter(BinanceConfig(enabled=True, environment="testnet", market="futures"))
    monkeypatch.setattr(adapter, "_execution_ready", lambda: None)
    monkeypatch.setattr(adapter, "get_account", lambda: {"canTrade": True, "availableBalance": "0"})
    monkeypatch.setattr(adapter, "_symbol_filters", lambda symbol: {})
    monkeypatch.setattr(adapter, "_market_price", lambda symbol: 50000.0)
    monkeypatch.setattr(adapter, "get_open_orders", lambda symbol=None: [])
    monkeypatch.setattr(adapter.gateway, "validate", lambda **kwargs: SimpleNamespace(
        ok=True, normalized={"notional_estimate": 50.0}, to_dict=lambda: {"ok": True}
    ))
    monkeypatch.setattr(adapter, "set_margin_type", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("reduce-only close must not change margin mode")
    ))
    monkeypatch.setattr(adapter, "set_leverage", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("reduce-only close must not change leverage")
    ))
    calls = []
    def fake_request(method, path, signed=False, params=None):
        calls.append((method, path, dict(params or {})))
        if path == "/fapi/v1/positionSide/dual":
            return {"dualSidePosition": False}
        return {"orderId": 456, "status": "FILLED"}
    monkeypatch.setattr(adapter, "_request", fake_request)

    result = adapter.place_order("BTCUSDT", "SELL", "MARKET", 0.001, reduce_only=True)

    assert result["ok"] is True
    assert result["sent_to_exchange"] is True
    order = next(call for call in calls if call[1] == "/fapi/v1/order")
    assert order[2]["reduceOnly"] == "true"
    assert not any(path in {"/fapi/v1/marginType", "/fapi/v1/leverage"} for _, path, _ in calls)


def test_futures_open_order_is_blocked_if_exchange_refuses_isolated_margin(monkeypatch):
    adapter = BinanceAdapter(BinanceConfig(enabled=True, environment="testnet", market="futures"))
    monkeypatch.setattr(adapter, "_api_key", lambda: "testnet-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "testnet-secret")
    calls = []

    def fake_request(method, path, signed=False, params=None):
        calls.append(path)
        if path == "/fapi/v1/positionSide/dual":
            return {"dualSidePosition": False}
        if path == "/fapi/v2/account":
            return {"canTrade": True, "availableBalance": "1000"}
        if path == "/fapi/v1/openOrders":
            return []
        if path == "/fapi/v1/ticker/price":
            return {"symbol": "BTCUSDT", "price": "50000"}
        if path == "/fapi/v1/exchangeInfo":
            return {"symbols": [{"symbol": "BTCUSDT", "status": "TRADING", "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                {"filterType": "MIN_NOTIONAL", "notional": "5"}]}]}
        if path == "/fapi/v1/marginType":
            return {"error": "http_error", "status": 400,
                    "body": '{"code":-4047,"msg":"Position exists."}'}
        raise AssertionError("leverage/order must not be sent: " + path)

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.place_order("BTCUSDT", "BUY", "MARKET", 0.001, leverage=5)

    assert result["ok"] is False
    assert result["sent_to_exchange"] is False
    assert result["error"] == "isolated_margin_setup_failed"
    assert "/fapi/v1/marginType" in calls
    assert "/fapi/v1/leverage" not in calls
    assert "/fapi/v1/order" not in calls
