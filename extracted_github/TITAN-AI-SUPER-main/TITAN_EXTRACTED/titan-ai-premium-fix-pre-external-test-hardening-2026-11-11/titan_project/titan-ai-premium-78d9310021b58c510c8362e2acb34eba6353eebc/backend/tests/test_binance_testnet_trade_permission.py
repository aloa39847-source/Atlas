from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app import star_app
from app.integrations.binance_adapter import BinanceConfig, BinanceTestnetAdapter


def _adapter(market="spot"):
    return BinanceTestnetAdapter(BinanceConfig(
        enabled=True,
        environment="testnet",
        market=market,
        api_key_ref="masked-testnet-key",
        api_secret_ref="masked-testnet-secret",
    ))


def test_spot_trade_scope_validation_uses_order_test_and_never_real_order(monkeypatch):
    adapter = _adapter("spot")
    monkeypatch.setattr(adapter, "_api_key", lambda: "testnet-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "testnet-secret")
    monkeypatch.setattr(adapter, "status", lambda: {
        "connected": True, "account_can_trade": True,
    })
    monkeypatch.setattr(adapter, "_symbol_filters", lambda symbol: {
        "marketStepSize": 0.001, "marketMinQty": 0.001,
        "marketMaxQty": 100, "minNotional": 5,
    })
    monkeypatch.setattr(adapter, "_market_price", lambda symbol: 60000.0)
    calls = []

    def fake_request(method, path, signed=False, params=None):
        calls.append((method, path, signed, dict(params or {})))
        return {}

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.validate_testnet_trade_permission("BTCUSDT")

    assert result["ok"] is True
    assert result["verified"] is True
    assert result["order_submitted"] is False
    assert result["matching_engine_reached"] is False
    assert calls == [("POST", "/api/v3/order/test", True, {
        "symbol": "BTCUSDT", "side": "BUY", "type": "MARKET",
        "quantity": "0.001", "newOrderRespType": "ACK",
    })]
    assert all("/order/test" in call[1] for call in calls)


def test_futures_trade_scope_validation_respects_hedge_position_mode(monkeypatch):
    adapter = _adapter("futures")
    monkeypatch.setattr(adapter, "_api_key", lambda: "futures-testnet-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "futures-testnet-secret")
    monkeypatch.setattr(adapter, "status", lambda: {
        "connected": True, "account_can_trade": True,
    })
    monkeypatch.setattr(adapter, "_symbol_filters", lambda symbol: {
        "marketStepSize": 0.001, "marketMinQty": 0.001,
        "marketMaxQty": 100, "minNotional": 5,
    })
    monkeypatch.setattr(adapter, "_market_price", lambda symbol: 60000.0)
    calls = []

    def fake_request(method, path, signed=False, params=None):
        calls.append((method, path, signed, dict(params or {})))
        if path == "/fapi/v1/positionSide/dual":
            return {"dualSidePosition": True}
        return {}

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.validate_testnet_trade_permission("BTCUSDT")

    assert result["ok"] is True
    assert result["order_submitted"] is False
    assert calls[0] == ("GET", "/fapi/v1/positionSide/dual", True, {})
    assert calls[1] == ("POST", "/fapi/v1/order/test", True, {
        "symbol": "BTCUSDT", "side": "BUY", "type": "MARKET",
        "quantity": "0.001", "newOrderRespType": "ACK", "positionSide": "LONG",
    })


def test_testnet_trade_permission_cache_is_scoped_to_key_and_market(monkeypatch):
    adapter = _adapter("spot")
    monkeypatch.setattr(adapter, "_api_key", lambda: "testnet-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "testnet-secret")
    monkeypatch.setattr(adapter, "status", lambda: {
        "connected": True, "account_can_trade": True,
    })
    monkeypatch.setattr(adapter, "_symbol_filters", lambda symbol: {
        "marketStepSize": 0.001, "marketMinQty": 0.001, "marketMaxQty": 100,
        "minNotional": 0,
    })
    monkeypatch.setattr(adapter, "_market_price", lambda symbol: 60000.0)
    monkeypatch.setattr(adapter, "_request", lambda *args, **kwargs: {})
    assert adapter.validate_testnet_trade_permission("BTCUSDT")["ok"] is True
    assert adapter._testnet_trade_permission_is_cached() is True

    other_key = _adapter("spot")
    monkeypatch.setattr(other_key, "_api_key", lambda: "different-testnet-key")
    monkeypatch.setattr(other_key, "_api_secret", lambda: "different-testnet-secret")
    assert other_key._testnet_trade_permission_is_cached() is False

    futures_key = _adapter("futures")
    monkeypatch.setattr(futures_key, "_api_key", lambda: "testnet-key")
    monkeypatch.setattr(futures_key, "_api_secret", lambda: "testnet-secret")
    assert futures_key._testnet_trade_permission_is_cached() is False


def test_live_adapter_cannot_use_testnet_permission_validation(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="live", market="spot",
        api_key_ref="masked-live-key", api_secret_ref="masked-live-secret",
    ))
    calls = []
    monkeypatch.setattr(adapter, "_request", lambda *args, **kwargs: calls.append(args))
    result = adapter.validate_testnet_trade_permission("BTCUSDT")
    assert result["ok"] is False
    assert result["order_submitted"] is False
    assert result["error"] == "testnet_only_permission_validation"
    assert calls == []


def test_api_endpoint_uses_selected_testnet_market_and_never_returns_secrets(monkeypatch):
    adapter = SimpleNamespace(validate_testnet_trade_permission=lambda symbol: {
        "ok": True, "verified": True, "order_submitted": False,
        "matching_engine_reached": False, "symbol": symbol, "market": "futures",
    })
    monkeypatch.setattr(star_app, "_binance_auth_error", lambda request: None)
    monkeypatch.setattr(star_app, "body", lambda request: asyncio.sleep(0, result={
        "market": "futures", "symbol": "BTCUSDT",
    }))
    captured = []
    monkeypatch.setattr(star_app, "_get_binance_adapter",
                        lambda market: captured.append(market) or adapter)
    response = asyncio.run(star_app.binance_testnet_validate_trade_permission(
        SimpleNamespace(query_params={}),
    ))
    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["order_submitted"] is False
    assert captured == ["futures"]


def test_status_reports_ready_only_after_cached_order_test_validation(monkeypatch):
    validator = _adapter("spot")
    monkeypatch.setattr(validator, "_api_key", lambda: "cached-testnet-key")
    monkeypatch.setattr(validator, "_api_secret", lambda: "cached-testnet-secret")
    monkeypatch.setattr(validator, "status", lambda: {
        "connected": True, "account_can_trade": True,
    })
    monkeypatch.setattr(validator, "_symbol_filters", lambda symbol: {
        "marketStepSize": 0.001, "marketMinQty": 0.001, "marketMaxQty": 100,
        "minNotional": 0,
    })
    monkeypatch.setattr(validator, "_market_price", lambda symbol: 60000.0)
    monkeypatch.setattr(validator, "_request", lambda *args, **kwargs: {})
    assert validator.validate_testnet_trade_permission("BTCUSDT")["ok"] is True

    status_adapter = _adapter("spot")
    monkeypatch.setattr(status_adapter, "_api_key", lambda: "cached-testnet-key")
    monkeypatch.setattr(status_adapter, "_api_secret", lambda: "cached-testnet-secret")
    monkeypatch.setattr(status_adapter, "_request", lambda method, path, **kwargs: {
        "canTrade": True, "balances": [],
    })
    status = status_adapter.status()
    assert status["connected"] is True
    assert status["account_can_trade"] is True
    assert status["api_key_trade_permission_verified"] is True
    assert status["execution_ready"] is True
    assert status["execution_readiness_check"] == "ok"
