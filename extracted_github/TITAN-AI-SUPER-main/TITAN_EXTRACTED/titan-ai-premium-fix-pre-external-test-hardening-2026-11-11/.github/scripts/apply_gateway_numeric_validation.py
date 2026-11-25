from pathlib import Path
import sys

root = Path(sys.argv[1])
path = root / "app/engines/order_gateway.py"
s = path.read_text()

anchor = "from dataclasses import dataclass\nfrom typing import Any\n"
replacement = "from dataclasses import dataclass\nfrom math import isfinite\nfrom typing import Any\n"
if anchor in s:
    s = s.replace(anchor, replacement, 1)
elif "from math import isfinite" not in s:
    raise SystemExit("Could not locate imports")

old = """        warnings: list[str] = []
        comp = compliance_engine.check_order_action(
"""
new = """        warnings: list[str] = []

        # Fail closed before compliance checks or arithmetic: NaN compares false
        # against both bounds and can otherwise evade a notional ceiling.
        try:
            if isinstance(quantity, bool) or quantity is None:
                raise ValueError("quantity")
            quantity = float(quantity)
            if not isfinite(quantity) or quantity <= 0:
                raise ValueError("quantity")
            if price is not None:
                if isinstance(price, bool):
                    raise ValueError("price")
                price = float(price)
                if not isfinite(price) or price <= 0:
                    raise ValueError("price")
            if reference_price is not None:
                if isinstance(reference_price, bool):
                    raise ValueError("reference_price")
                reference_price = float(reference_price)
                if not isfinite(reference_price) or reference_price <= 0:
                    raise ValueError("reference_price")
            if max_notional is not None:
                if isinstance(max_notional, bool):
                    raise ValueError("max_notional")
                max_notional = float(max_notional)
                if not isfinite(max_notional) or max_notional <= 0:
                    raise ValueError("max_notional")
            if exchange_filters is not None:
                if not isinstance(exchange_filters, dict):
                    raise ValueError("exchange_filters")
                for filter_name in ("tickSize", "stepSize", "minNotional"):
                    raw_filter = exchange_filters.get(filter_name, 0) or 0
                    if isinstance(raw_filter, bool):
                        raise ValueError(filter_name)
                    filter_value = float(raw_filter)
                    if not isfinite(filter_value) or filter_value < 0:
                        raise ValueError(filter_name)
                    exchange_filters[filter_name] = filter_value
        except (TypeError, ValueError, OverflowError) as exc:
            field = str(exc) or "numeric_input"
            return GatewayDecision(False, f"invalid_numeric_input:{field}", {"field": field}, warnings)

        comp = compliance_engine.check_order_action(
"""
if old in s:
    s = s.replace(old, new, 1)
elif "Fail closed before compliance checks or arithmetic" not in s:
    raise SystemExit("Could not locate gateway validation insertion point")

path.write_text(s)

tests = r'''import math
import pytest

from app.core.enums import MarketType
from app.engines.order_gateway import PreTradeOrderGateway


@pytest.mark.parametrize("field,value", [
    ("quantity", math.nan), ("quantity", math.inf), ("quantity", -math.inf),
    ("price", math.nan), ("price", math.inf), ("price", -math.inf),
    ("reference_price", math.nan), ("reference_price", math.inf),
    ("max_notional", math.nan), ("max_notional", math.inf),
])
def test_gateway_rejects_non_finite_order_inputs_before_compliance(field, value):
    args = dict(symbol="BTCUSDT", market=MarketType.SPOT, side="buy",
                order_type="limit", quantity=0.001, price=50.0,
                reference_price=50.0, environment_mode="demo")
    args[field] = value
    result = PreTradeOrderGateway().validate(**args)
    assert result.ok is False
    assert result.reason.startswith("invalid_numeric_input:")


@pytest.mark.parametrize("field,value", [
    ("tickSize", math.nan), ("tickSize", math.inf),
    ("stepSize", -math.inf), ("minNotional", math.nan),
    ("minNotional", -1.0),
])
def test_gateway_rejects_invalid_exchange_filter_values(field, value):
    args = dict(symbol="BTCUSDT", market=MarketType.SPOT, side="buy",
                order_type="limit", quantity=0.001, price=50.0,
                reference_price=50.0, environment_mode="demo",
                exchange_filters={"tickSize": 0.01, "stepSize": 0.001, "minNotional": 5.0})
    args["exchange_filters"][field] = value
    result = PreTradeOrderGateway().validate(**args)
    assert result.ok is False
    assert result.reason.startswith("invalid_numeric_input:")


def test_gateway_rejects_boolean_numeric_values():
    result = PreTradeOrderGateway().validate(
        symbol="BTCUSDT", market=MarketType.SPOT, side="buy",
        order_type="limit", quantity=True, price=50.0,
        reference_price=50.0, environment_mode="demo")
    assert result.ok is False
    assert result.reason == "invalid_numeric_input:quantity"


def test_gateway_still_enforces_hard_spot_notional_cap():
    result = PreTradeOrderGateway().validate(
        symbol="BTCUSDT", market=MarketType.SPOT, side="buy",
        order_type="limit", quantity=2.0, price=50.0,
        reference_price=50.0, environment_mode="demo")
    assert result.ok is False
    assert result.reason == "notional_exceeds_limit"
'''
(root / "tests/test_gateway_numeric_validation.py").write_text(tests)
print("Pre-trade gateway numeric validation patch and tests applied.")
