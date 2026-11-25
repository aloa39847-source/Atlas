from app.core.enums import MarketType
from app.engines.compliance import TitanCompliancePolicyEngine
from app.engines.order_gateway import PreTradeOrderGateway


def test_withdrawal_permission_blocked():
    c = TitanCompliancePolicyEngine()
    r = c.check_api_permissions(["read", "enableWithdrawals"])
    assert r.ok is False and r.action == "block"


def test_bypass_attempt_blocked():
    c = TitanCompliancePolicyEngine()
    r = c.check_order_action(market=MarketType.SPOT, symbol="BTC/USDT", side="buy", quantity=1, price=1, environment_mode="demo", live_enabled=False, bypass_attempt=True)
    assert r.ok is False


def test_live_exchange_eligibility_is_delegated_to_authenticated_exchange_permissions():
    c = TitanCompliancePolicyEngine()
    r = c.check_order_action(
        market=MarketType.SPOT, symbol="BTC/USDT", side="buy",
        quantity=0.01, price=50000, environment_mode="live",
        live_enabled=True, geo_verified=None, kyc_verified=None,
        rate_limit_ok=True,
    )
    assert r.ok is True
    assert r.action == "allow_with_checks"
    assert "exchange_enforces_geographic_eligibility" in r.details["uncertain"]
    assert "exchange_enforces_account_verification" in r.details["uncertain"]


def test_explicitly_ineligible_live_account_is_still_blocked():
    c = TitanCompliancePolicyEngine()
    r = c.check_order_action(
        market=MarketType.SPOT, symbol="BTC/USDT", side="buy",
        quantity=0.01, price=50000, environment_mode="live",
        live_enabled=True, geo_verified=False, kyc_verified=True,
        rate_limit_ok=True,
    )
    assert r.ok is False
    assert r.action == "block"
