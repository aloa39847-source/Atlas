from app.core.enums import MarketType
from app.engines.compliance import compliance_engine
from app.engines.order_gateway import PreTradeOrderGateway


def test_compliance_blocks_explicit_prohibited_market_conduct_intent():
    for flag in (
        "wash_trading_intent", "self_trade_intent", "spoofing_intent",
        "layering_intent", "quote_stuffing_intent", "front_running_intent",
        "pump_and_dump_intent",
    ):
        result = compliance_engine.check_market_conduct({flag: True})
        assert result.ok is False
        assert result.action == "block"
        assert result.reason == "prohibited_market_conduct_intent"


def test_compliance_rejects_malformed_market_conduct_metadata():
    result = compliance_engine.check_market_conduct(["spoofing"])
    assert result.ok is False
    assert result.reason == "invalid_market_conduct_metadata"


def test_pretrade_gateway_blocks_prohibited_market_conduct_before_acceptance():
    result = PreTradeOrderGateway().validate(
        symbol="BTC/USDT", market=MarketType.SPOT, side="buy",
        order_type="limit", quantity=0.001, price=10, reference_price=10,
        market_conduct_flags={"layering_intent": True},
    )
    assert result.ok is False
    assert result.reason == "compliance_block:prohibited_market_conduct_intent"
