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


def test_live_without_preconditions_requires_review_or_block():
    g = PreTradeOrderGateway()
    r = g.validate(symbol="BTC/USDT", market=MarketType.SPOT, side="buy", order_type="limit", quantity=0.01, price=50000, reference_price=50000, environment_mode="live", live_enabled=True)
    assert r.ok is False
    assert "compliance" in str(r.normalized).lower() or "compliance" in r.reason
