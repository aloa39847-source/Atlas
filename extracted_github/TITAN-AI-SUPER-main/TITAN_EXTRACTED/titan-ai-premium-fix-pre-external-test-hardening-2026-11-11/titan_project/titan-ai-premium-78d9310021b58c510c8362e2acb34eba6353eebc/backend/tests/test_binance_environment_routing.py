from types import SimpleNamespace

from app.core.config import settings
from app.services import binance_execution as execution_module
from app.services.binance_execution import binance_execution_service
from app.integrations.binance_adapter import (
    BinanceConfig,
    BinanceTestnetAdapter,
    BinanceLiveAdapter,
    SPOT_LIVE_BASE,
    FUTURES_LIVE_BASE,
    SPOT_TESTNET_BASE,
    FUTURES_TESTNET_BASE,
)


def test_binance_adapter_selects_isolated_environment_endpoints():
    assert BinanceTestnetAdapter(BinanceConfig(environment="testnet", market="spot"))._base_url() == SPOT_TESTNET_BASE
    assert BinanceTestnetAdapter(BinanceConfig(environment="testnet", market="futures"))._base_url() == FUTURES_TESTNET_BASE
    assert BinanceTestnetAdapter(BinanceConfig(environment="live", market="spot"))._base_url() == SPOT_LIVE_BASE
    assert BinanceTestnetAdapter(BinanceConfig(environment="live", market="futures"))._base_url() == FUTURES_LIVE_BASE


def test_live_trading_is_disabled_by_default_without_explicit_server_override(monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("TITAN_LIVE_TRADING_ENABLED", raising=False)
    assert Settings().live_trading_enabled is False


def test_live_trading_can_only_be_enabled_by_explicit_server_override(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("TITAN_LIVE_TRADING_ENABLED", "true")
    assert Settings().live_trading_enabled is True


def test_live_execution_requires_server_enablement_user_confirmation_and_worker(monkeypatch):
    monkeypatch.setattr(settings, "live_trading_enabled", True)
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="live", market="spot",
        live_confirmed_by_user=True, live_geo_eligible_confirmed=True,
        live_kyc_confirmed=True, live_rate_limit_control_enabled=True,
        live_worker_ready=True,
    ))
    monkeypatch.setattr(adapter, "_api_key", lambda: "masked-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "masked-secret")
    assert adapter._execution_ready() is None

    unconfirmed = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="live", market="spot",
        live_confirmed_by_user=False, live_worker_ready=True,
    ))
    monkeypatch.setattr(unconfirmed, "_api_key", lambda: "masked-key")
    monkeypatch.setattr(unconfirmed, "_api_secret", lambda: "masked-secret")
    assert unconfirmed._execution_ready()["error"] == "live_requires_explicit_user_confirmation"


def test_live_adapter_direct_execution_honors_server_global_disable(monkeypatch):
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="live", market="spot",
        live_confirmed_by_user=True, live_worker_ready=True,
    ))
    monkeypatch.setattr(adapter, "_api_key", lambda: "masked-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "masked-secret")
    blocked = adapter._execution_ready()
    assert blocked is not None
    assert blocked["sent_to_exchange"] is False
    assert blocked["error"] == "live_trading_disabled_by_server"


def test_live_status_performs_read_only_account_and_permission_checks(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="live", market="spot",
        api_key_ref="env:BINANCE_LIVE_API_KEY",
        api_secret_ref="env:BINANCE_LIVE_API_SECRET",
    ))
    monkeypatch.setattr(adapter, "_api_key", lambda: "masked-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "masked-secret")
    calls = []
    def fake_request(method, path, signed=False, params=None):
        calls.append((method, path, signed))
        if path == "/api/v3/account":
            return {"canTrade": True, "balances": []}
        if path == "/sapi/v1/account/apiRestrictions":
            return {"ipRestrict": True, "enableSpotAndMarginTrading": True,
                    "enableWithdrawals": False, "enableInternalTransfer": False}
        raise AssertionError("Unexpected Live request")
    monkeypatch.setattr(adapter, "_request", fake_request)
    status = adapter.status()
    assert status["configured"] is True
    assert status["connected"] is True
    assert status["permissions_verified"] is True
    assert status["execution_ready"] is False
    assert calls == [("GET", "/api/v3/account", True),
                     ("GET", "/sapi/v1/account/apiRestrictions", True)]


def test_testnet_account_read_does_not_claim_api_key_trade_permission(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="testnet", market="spot",
        api_key_ref="env:BINANCE_TESTNET_API_KEY",
        api_secret_ref="env:BINANCE_TESTNET_API_SECRET",
    ))
    monkeypatch.setattr(adapter, "_api_key", lambda: "masked-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "masked-secret")
    calls = []
    def fake_request(method, path, signed=False, params=None):
        calls.append((method, path, signed))
        return {"canTrade": True, "balances": []}
    monkeypatch.setattr(adapter, "_request", fake_request)
    status = adapter.status()
    assert status["connected"] is True
    assert status["account_can_trade"] is True
    assert status["trade_permission"] is None
    assert status["api_key_trade_permission_verified"] is None
    assert status["execution_ready"] is False
    assert status["execution_readiness_check"] == "api_key_trade_permission_requires_explicit_test_order_validation"
    assert calls == [("GET", "/api/v3/account", True)]


def test_spot_status_rejects_malformed_account_payload(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="testnet", market="spot",
        api_key_ref="env:BINANCE_TESTNET_API_KEY",
        api_secret_ref="env:BINANCE_TESTNET_API_SECRET",
    ))
    monkeypatch.setattr(adapter, "_api_key", lambda: "masked-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "masked-secret")
    monkeypatch.setattr(adapter, "_request", lambda *args, **kwargs: {"unexpected": "payload"})
    status = adapter.status()
    assert status["configured"] is True
    assert status["connected"] is False
    assert status["account_check"] == "invalid_or_error_response"
    assert status["execution_ready"] is False


def test_futures_status_rejects_malformed_account_payload(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="testnet", market="futures",
        api_key_ref="env:BINANCE_TESTNET_FUTURES_API_KEY",
        api_secret_ref="env:BINANCE_TESTNET_FUTURES_API_SECRET",
    ))
    monkeypatch.setattr(adapter, "_api_key", lambda: "masked-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "masked-secret")
    monkeypatch.setattr(adapter, "_request", lambda *args, **kwargs: {"canTrade": True, "positions": []})
    status = adapter.status()
    assert status["configured"] is True
    assert status["connected"] is False
    assert status["account_check"] == "invalid_or_error_response"
    assert status["execution_ready"] is False

def test_invalid_binance_market_never_falls_back_to_spot():
    adapter = BinanceTestnetAdapter(BinanceConfig(environment="testnet", market="futuresx"))
    try:
        adapter._base_url()
        assert False, "Unsupported market must not select Spot endpoint"
    except RuntimeError as exc:
        assert "unsupported_binance_market" in str(exc)
    assert adapter.status()["error"] == "unsupported_binance_market"


def test_live_status_keeps_withdrawal_and_transfer_permissions_unknown_until_verified(monkeypatch):
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="live", market="spot",
        api_key_ref="env:BINANCE_LIVE_API_KEY",
        api_secret_ref="env:BINANCE_LIVE_API_SECRET",
    ))
    monkeypatch.setattr(adapter, "_api_key", lambda: "masked-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "masked-secret")
    def fake_request(method, path, signed=False, params=None):
        if path == "/api/v3/account":
            return {"canTrade": True, "balances": []}
        if path == "/sapi/v1/account/apiRestrictions":
            return {"error": "http_error", "status": 451}
        raise AssertionError("Unexpected Live request")
    monkeypatch.setattr(adapter, "_request", fake_request)
    status = adapter.status()
    assert status["connected"] is True
    assert status["permissions_verified"] is False
    assert status["withdrawal_permissions"] is None
    assert status["transfer_permissions"] is None
    assert status["execution_ready"] is False


def test_binance_credentials_are_separated_by_environment_and_market(tmp_path, monkeypatch):
    from app.services import credentials

    monkeypatch.setenv("TITAN_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "secret_key", "strong-test-secret-key-for-titan-" + "X" * 32)

    credentials.save_testnet_credentials("testnet-spot-key", "testnet-spot-secret", "spot")
    credentials.save_testnet_credentials("testnet-futures-key", "testnet-futures-secret", "futures")
    credentials.save_live_credentials("live-spot-key", "live-spot-secret", "spot")
    credentials.save_live_credentials("live-futures-key", "live-futures-secret", "futures")

    assert credentials.load_testnet_credentials("spot")["api_key"] == "testnet-spot-key"
    assert credentials.load_testnet_credentials("futures")["api_key"] == "testnet-futures-key"
    assert credentials.load_binance_credentials("live", "spot")["api_key"] == "live-spot-key"
    assert credentials.load_binance_credentials("live", "futures")["api_key"] == "live-futures-key"



def test_dedicated_live_adapter_rejects_testnet_configuration():
    try:
        BinanceLiveAdapter(BinanceConfig(environment="testnet", market="spot"))
        assert False, "Live adapter must reject Testnet configuration"
    except ValueError as exc:
        assert "requires_live_environment" in str(exc)


def test_futures_maintenance_brackets_are_signed_read_only_and_testnet_only(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=False, environment="testnet", market="futures",
    ))
    monkeypatch.setattr(adapter, "_api_key", lambda: "testnet-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "testnet-secret")
    calls = []
    monkeypatch.setattr(adapter, "_request", lambda method, path, signed=False, params=None: (
        calls.append((method, path, signed, params)) or {
            "symbol": "BTCUSDT",
            "brackets": [
                {"bracket": 1, "initialLeverage": 125, "notionalFloor": "0",
                 "notionalCap": "50000", "maintMarginRatio": "0.004", "cum": "0"},
                {"bracket": 2, "initialLeverage": 100, "notionalFloor": "50000",
                 "notionalCap": "250000", "maintMarginRatio": "0.01", "cum": "300"},
            ],
        }
    ))
    result = adapter.get_futures_maintenance_brackets("BTC/USDT")
    assert result["ok"] is True
    assert result["source"] == "binance_futures_testnet_signed_read_only"
    assert result["maintenance_brackets"][1]["maintMarginRatio"] == 0.01
    assert result["no_orders_submitted"] is True
    assert calls == [("GET", "/fapi/v1/leverageBracket", True, {"symbol": "BTCUSDT"})]


def test_futures_maintenance_brackets_never_use_live_or_spot_credentials(monkeypatch):
    for config in (
        BinanceConfig(environment="live", market="futures"),
        BinanceConfig(environment="testnet", market="spot"),
    ):
        adapter = BinanceTestnetAdapter(config)
        monkeypatch.setattr(adapter, "_api_key", lambda: "key")
        monkeypatch.setattr(adapter, "_api_secret", lambda: "secret")
        try:
            adapter.get_futures_maintenance_brackets("BTCUSDT")
            assert False, "Only Futures Testnet bracket retrieval is permitted"
        except RuntimeError as exc:
            assert "testnet_futures_only" in str(exc)


def test_futures_maintenance_brackets_reject_missing_tier_schedule(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(environment="testnet", market="futures"))
    monkeypatch.setattr(adapter, "_api_key", lambda: "key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "secret")
    monkeypatch.setattr(adapter, "_request", lambda *args, **kwargs: [
        {"symbol": "BTCUSDT", "brackets": []},
    ])
    try:
        adapter.get_futures_maintenance_brackets("BTCUSDT")
        assert False, "An empty schedule must not be accepted"
    except ValueError as exc:
        assert "maintenance_brackets_required" in str(exc)


def test_futures_maintenance_brackets_accept_all_symbols_array_response(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(environment="testnet", market="futures"))
    monkeypatch.setattr(adapter, "_api_key", lambda: "key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "secret")
    monkeypatch.setattr(adapter, "_request", lambda *args, **kwargs: [
        {"symbol": "ETHUSDT", "brackets": [
            {"notionalFloor": 0, "notionalCap": 100000, "maintMarginRatio": 0.005, "cum": 0},
        ]},
        {"symbol": "BTCUSDT", "brackets": [
            {"notionalFloor": 0, "notionalCap": 50000, "maintMarginRatio": 0.004, "cum": 0},
        ]},
    ])
    result = adapter.get_futures_maintenance_brackets("BTCUSDT")
    assert result["symbol"] == "BTCUSDT"
    assert result["maintenance_brackets"][0]["notionalCap"] == 50000


def test_futures_maintenance_brackets_fail_closed_on_adjusted_notional_coefficient(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(environment="testnet", market="futures"))
    monkeypatch.setattr(adapter, "_api_key", lambda: "key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "secret")
    monkeypatch.setattr(adapter, "_request", lambda *args, **kwargs: {
        "symbol": "BTCUSDT", "notionalCoef": "1.5",
        "brackets": [{"notionalFloor": 0, "notionalCap": 50000,
                      "maintMarginRatio": 0.004, "cum": 0}],
    })
    try:
        adapter.get_futures_maintenance_brackets("BTCUSDT")
        assert False, "Adjusted user-specific tiers must not be used without verified coefficient semantics"
    except RuntimeError as exc:
        assert "adjusted_notional_coefficient_not_supported" in str(exc)


def test_testnet_environment_fallback_never_reuses_spot_keys_for_futures(monkeypatch):
    from app import star_app

    monkeypatch.setattr(star_app, "load_testnet_credentials", lambda market: {"configured": False})
    monkeypatch.setenv("BINANCE_TESTNET_MARKET", "spot")
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "spot-key")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "spot-secret")
    monkeypatch.delenv("BINANCE_TESTNET_FUTURES_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_TESTNET_FUTURES_API_SECRET", raising=False)
    adapter = star_app._get_binance_adapter("futures")
    assert adapter.config.api_key_ref is None
    assert adapter.config.api_secret_ref is None
    assert adapter.config.enabled is False


def test_testnet_futures_environment_specific_keys_are_selected(monkeypatch):
    from app import star_app

    monkeypatch.setattr(star_app, "load_testnet_credentials", lambda market: {"configured": False})
    monkeypatch.setenv("BINANCE_TESTNET_FUTURES_API_KEY", "futures-key")
    monkeypatch.setenv("BINANCE_TESTNET_FUTURES_API_SECRET", "futures-secret")
    adapter = star_app._get_binance_adapter("futures")
    assert adapter.config.api_key_ref == "env:BINANCE_TESTNET_FUTURES_API_KEY"
    assert adapter.config.api_secret_ref == "env:BINANCE_TESTNET_FUTURES_API_SECRET"
    assert adapter.config.enabled is True



def test_supervised_worker_does_not_reuse_spot_testnet_environment_keys_for_futures(monkeypatch):
    monkeypatch.setattr(execution_module, "load_testnet_credentials", lambda market: {"configured": False})
    monkeypatch.setenv("BINANCE_TESTNET_MARKET", "spot")
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "spot-key")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "spot-secret")
    monkeypatch.delenv("BINANCE_TESTNET_FUTURES_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_TESTNET_FUTURES_API_SECRET", raising=False)
    state = SimpleNamespace(account_target="testnet", market_type="futures")
    adapter = binance_execution_service.adapter_for(state)
    assert adapter.config.enabled is False
    assert adapter.config.api_key_ref is None
    assert adapter.config.api_secret_ref is None


def test_supervised_worker_uses_market_specific_futures_testnet_environment_keys(monkeypatch):
    monkeypatch.setattr(execution_module, "load_testnet_credentials", lambda market: {"configured": False})
    monkeypatch.setenv("BINANCE_TESTNET_MARKET", "spot")
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "spot-key")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "spot-secret")
    monkeypatch.setenv("BINANCE_TESTNET_FUTURES_API_KEY", "futures-key")
    monkeypatch.setenv("BINANCE_TESTNET_FUTURES_API_SECRET", "futures-secret")
    state = SimpleNamespace(account_target="testnet", market_type="futures")
    adapter = binance_execution_service.adapter_for(state)
    assert adapter.config.enabled is True
    assert adapter.config.api_key_ref == "env:BINANCE_TESTNET_FUTURES_API_KEY"
    assert adapter.config.api_secret_ref == "env:BINANCE_TESTNET_FUTURES_API_SECRET"


def test_supervised_worker_routes_live_credentials_to_live_endpoints(monkeypatch):
    monkeypatch.setattr(execution_module, "load_binance_credentials", lambda *args: {
        "configured": True, "api_key": "live-key", "api_secret": "live-secret",
    })
    monkeypatch.setattr(settings, "live_trading_enabled", True)
    state = SimpleNamespace(account_target="live", market_type="spot", live_confirmed_by_user=False)
    adapter = binance_execution_service.adapter_for(state, live_worker_ready=False)
    assert adapter.config.environment == "live"
    assert adapter.config.market == "spot"
    assert adapter.config.api_key_ref == "stored:live:spot:api_key"
    assert adapter._base_url() == SPOT_LIVE_BASE


def test_execution_service_rejects_live_without_explicit_user_confirmation(monkeypatch):
    monkeypatch.setattr(settings, "live_trading_enabled", True)
    state = SimpleNamespace(account_target="live", market_type="spot", live_trading_enabled=True, live_confirmed_by_user=False)
    class FakeDB:
        def get(self, model, ident):
            return state
        def execute(self, *args, **kwargs):
            raise AssertionError("No order lookup should occur before Live confirmation")
    result = binance_execution_service.execute_order(
        FakeDB(), symbol="BTCUSDT", side="BUY", order_type="MARKET",
        quantity=0.001, idempotency_key="live-confirm-test-0001",
    )
    assert result["ok"] is False
    assert result["sent_to_exchange"] is False
    assert result["error"] == "live_requires_explicit_user_confirmation"


def test_live_adapter_transport_allows_signed_read_only_account_get(monkeypatch):
    import io
    adapter = BinanceLiveAdapter(BinanceConfig(
        enabled=True, environment="live", market="spot",
        api_key_ref="env:BINANCE_LIVE_API_KEY",
        api_secret_ref="env:BINANCE_LIVE_API_SECRET",
    ))
    monkeypatch.setattr(adapter, "_api_key", lambda: "masked-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "masked-secret")
    calls = []
    def fake_urlopen(request, timeout=15):
        calls.append((request.full_url, request.method, timeout))
        return io.BytesIO(b'{"canTrade":true,"balances":[]}')
    monkeypatch.setattr("app.integrations.binance_adapter.urllib.request.urlopen", fake_urlopen)
    result = adapter.get_account()
    assert result["canTrade"] is True
    assert calls and calls[0][0].startswith(SPOT_LIVE_BASE + "/api/v3/account?")
    assert calls[0][1] == "GET"


def test_testnet_cancel_order_uses_signed_delete_and_exchange_order_identifier(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="testnet", market="spot",
    ))
    monkeypatch.setattr(adapter, "_api_key", lambda: "testnet-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "testnet-secret")
    calls = []
    def fake_request(method, path, signed=False, params=None):
        calls.append((method, path, signed, params))
        return {"symbol": "BTCUSDT", "orderId": 123, "status": "CANCELED",
                "executedQty": "0", "origQty": "0.001"}
    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.cancel_order("BTC/USDT", order_id=123, client_order_id="titan-client-123")
    assert result["ok"] is True
    assert result["sent_to_exchange"] is True
    assert calls == [("DELETE", "/api/v3/order", True, {
        "symbol": "BTCUSDT", "orderId": 123,
        "origClientOrderId": "titan-client-123",
    })]


def test_live_status_respects_server_global_kill_switch(monkeypatch):
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="live", market="spot",
        live_confirmed_by_user=True, live_worker_ready=True,
    ))
    monkeypatch.setattr(adapter, "_api_key", lambda: "masked-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "masked-secret")
    calls = []
    def fake_request(method, path, signed=False, params=None):
        calls.append((method, path, signed))
        if path == "/api/v3/account":
            return {"canTrade": True, "balances": []}
        if path == "/sapi/v1/account/apiRestrictions":
            return {"ipRestrict": True, "ipList": ["203.0.113.10"],
                    "enableSpotAndMarginTrading": True,
                    "enableWithdrawals": False, "enableInternalTransfer": False,
                    "permitsUniversalTransfer": False}
        raise AssertionError("Unexpected Live request")
    monkeypatch.setattr(adapter, "_request", fake_request)
    status = adapter.status()
    assert status["connected"] is True
    assert status["trade_permission"] is True
    assert status["permissions_verified"] is True
    assert status["execution_ready"] is False
    assert status["live_blocked"] is True
    assert calls == [("GET", "/api/v3/account", True),
                     ("GET", "/sapi/v1/account/apiRestrictions", True)]


def test_live_adapter_does_not_require_extra_server_or_geo_kyc_environment_flags(monkeypatch):
    monkeypatch.setattr(settings, "live_trading_enabled", True)
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="live", market="spot",
        live_confirmed_by_user=True, live_worker_ready=True,
    ))
    monkeypatch.setattr(adapter, "_api_key", lambda: "masked-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "masked-secret")
    assert adapter._execution_ready() is None
