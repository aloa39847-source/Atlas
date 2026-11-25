from pathlib import Path
import sys

root = Path(sys.argv[1])
path = root / "app/engines/order_gateway.py"
s = path.read_text()

old = '''        if ot not in {"market", "limit", "stop", "stop_limit"}:
            warnings.append("order_type_not_fully_verified_for_exchange")
        if quantity is None or quantity <= 0:
            return GatewayDecision(False, "invalid_quantity", {"quantity": quantity}, warnings)
        px = price if price is not None else reference_price
        if px is not None and px <= 0:
            return GatewayDecision(False, "invalid_price", {"price": price}, warnings)
        if reference_price and price and reference_price > 0:
            deviation = abs(price - reference_price) / reference_price
            if deviation > 0.2:
                return GatewayDecision(False, "price_deviation_too_large", {"deviation": deviation}, warnings)
            if deviation > 0.03:
                warnings.append("price_deviates_from_reference")
'''
new = '''        if ot not in {"market", "limit"}:
            return GatewayDecision(False, "unsupported_order_type", {"order_type": ot}, warnings)
        if quantity is None or quantity <= 0:
            return GatewayDecision(False, "invalid_quantity", {"quantity": quantity}, warnings)
        if ot == "limit" and price is None:
            return GatewayDecision(False, "limit_order_price_required", {"order_type": ot}, warnings)
        if ot == "market" and reference_price is None:
            return GatewayDecision(False, "reference_price_required_for_notional_cap", {"order_type": ot}, warnings)
        # A market order executes at the market, never at a caller-supplied limit/display price.
        # Its cap and min-notional calculations must use the trusted reference feed.
        px = reference_price if ot == "market" else price
        if px is not None and px <= 0:
            return GatewayDecision(False, "invalid_price", {"price": price}, warnings)
        if ot == "limit" and reference_price is not None and reference_price > 0:
            deviation = abs(price - reference_price) / reference_price
            if deviation > 0.2:
                return GatewayDecision(False, "price_deviation_too_large", {"deviation": deviation}, warnings)
            if deviation > 0.03:
                warnings.append("price_deviates_from_reference")
        if ot == "market" and price is not None:
            warnings.append("caller_supplied_market_price_ignored")
'''
if old in s:
    s = s.replace(old, new, 1)
elif "caller_supplied_market_price_ignored" not in s:
    raise SystemExit("Could not locate order type and price source logic")

path.write_text(s)

test_path = root / "tests/test_gateway_numeric_validation.py"
t = test_path.read_text()
if "test_market_order_notional_uses_trusted_reference_not_caller_price" not in t:
    t += '''

def test_market_order_notional_uses_trusted_reference_not_caller_price():
    result = PreTradeOrderGateway().validate(
        symbol="BTCUSDT", market=MarketType.SPOT, side="buy",
        order_type="market", quantity=0.002, price=1.0,
        reference_price=50000.0, environment_mode="demo")
    assert result.ok is False
    assert result.reason == "notional_exceeds_limit"


def test_market_order_ignores_caller_price_when_within_notional_cap():
    result = PreTradeOrderGateway().validate(
        symbol="BTCUSDT", market=MarketType.SPOT, side="buy",
        order_type="market", quantity=0.001, price=1.0,
        reference_price=50000.0, environment_mode="demo")
    assert result.ok is True
    assert result.normalized["notional_estimate"] == 50.0
    assert "caller_supplied_market_price_ignored" in result.warnings


def test_gateway_rejects_order_types_without_complete_exchange_parameters():
    for order_type in ("stop", "stop_limit", "trailing_stop_market", "unknown"):
        result = PreTradeOrderGateway().validate(
            symbol="BTCUSDT", market=MarketType.SPOT, side="buy",
            order_type=order_type, quantity=0.001, price=50.0,
            reference_price=50.0, environment_mode="demo")
        assert result.ok is False
        assert result.reason == "unsupported_order_type"


def test_gateway_requires_limit_price_and_trusted_market_reference():
    gateway = PreTradeOrderGateway()
    missing_limit = gateway.validate(
        symbol="BTCUSDT", market=MarketType.SPOT, side="buy",
        order_type="limit", quantity=0.001, reference_price=50.0)
    missing_market_ref = gateway.validate(
        symbol="BTCUSDT", market=MarketType.SPOT, side="buy",
        order_type="market", quantity=0.001)
    assert missing_limit.reason == "limit_order_price_required"
    assert missing_market_ref.reason == "reference_price_required_for_notional_cap"
'''
test_path.write_text(t)

print("Market-order reference pricing and supported order-type guards applied.")
