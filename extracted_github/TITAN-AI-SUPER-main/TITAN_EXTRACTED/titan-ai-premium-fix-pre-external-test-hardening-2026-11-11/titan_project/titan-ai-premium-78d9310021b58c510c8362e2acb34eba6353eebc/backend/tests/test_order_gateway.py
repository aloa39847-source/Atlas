from app.core.enums import MarketType
from app.engines.order_gateway import PreTradeOrderGateway


def test_spot_valid_order_passes_precheck():
    g = PreTradeOrderGateway()
    r = g.validate(symbol="BTC/USDT", market=MarketType.SPOT, side="buy", order_type="limit", quantity=0.001, price=50000, reference_price=50050)
    assert r.ok is True
    assert r.normalized["exchange_filters_checked"] is False


def test_earn_rejected():
    g = PreTradeOrderGateway()
    r = g.validate(symbol="BTC/USDT", market=MarketType.EARN, side="buy", order_type="market", quantity=1)
    assert r.ok is False and r.reason == "earn_not_order_execution"


def test_price_deviation_rejected():
    g = PreTradeOrderGateway()
    r = g.validate(symbol="BTC/USDT", market=MarketType.SPOT, side="buy", order_type="limit", quantity=1, price=100, reference_price=50000)
    assert r.ok is False



def test_unsupported_order_type_is_rejected_by_pretrade_gateway():
    """Unknown order types must fail closed instead of passing with a warning (CI regression)."""
    result = PreTradeOrderGateway().validate(
        symbol="BTC/USDT",
        market=MarketType.SPOT,
        side="buy",
        order_type="trailing_market",
        quantity=0.001,
        price=50000,
        reference_price=50000,
    )
    assert result.ok is False
    assert result.reason == "unsupported_order_type"



def test_gateway_rejects_non_finite_order_values():
    import math

    g = PreTradeOrderGateway()
    for bad in (math.nan, math.inf, -math.inf):
        result = g.validate(
            symbol="BTC/USDT", market=MarketType.SPOT, side="buy",
            order_type="limit", quantity=0.01, price=bad,
        )
        assert result.ok is False
        assert result.reason == "non_finite_numeric_input"


def test_gateway_rejects_malformed_numeric_values_without_raising():
    result = PreTradeOrderGateway().validate(
        symbol="BTC/USDT", market=MarketType.SPOT, side="buy",
        order_type="market", quantity="not-a-number",
    )
    assert result.ok is False
    assert result.reason == "invalid_numeric_input"


def test_gateway_rejects_invalid_reference_and_cap_values():
    g = PreTradeOrderGateway()
    bad_reference = g.validate(
        symbol="BTC/USDT", market=MarketType.SPOT, side="buy",
        order_type="market", quantity=0.01, reference_price=0,
    )
    bad_cap = g.validate(
        symbol="BTC/USDT", market=MarketType.SPOT, side="buy",
        order_type="limit", quantity=0.001, price=10, max_notional=0,
    )
    assert bad_reference.reason == "invalid_reference_price"
    assert bad_cap.reason == "invalid_max_notional"


def test_gateway_rejects_non_finite_exchange_filters():
    result = PreTradeOrderGateway().validate(
        symbol="BTC/USDT", market=MarketType.SPOT, side="buy",
        order_type="limit", quantity=0.001, price=10,
        exchange_filters={"tickSize": float("nan"), "stepSize": 0.001, "minNotional": 5},
    )
    assert result.ok is False
    assert result.reason == "invalid_exchange_filters"


def test_gateway_rejects_boolean_numeric_inputs_and_non_boolean_reduce_only():
    gateway = PreTradeOrderGateway()
    for field in ("quantity", "price", "reference_price", "max_notional", "leverage"):
        args = dict(symbol="BTC/USDT", market=MarketType.FUTURES, side="buy",
                    order_type="limit", quantity=0.01, price=100, reference_price=100)
        args[field] = True
        result = gateway.validate(**args)
        assert result.ok is False
        assert result.reason == "boolean_numeric_input_not_allowed"
    result = gateway.validate(
        symbol="BTC/USDT", market=MarketType.FUTURES, side="buy",
        order_type="market", quantity=0.01, price=100, reduce_only="false",
    )
    assert result.ok is False
    assert result.reason == "reduce_only_must_be_boolean"


def test_gateway_rejects_malformed_or_injectable_symbols():
    gateway = PreTradeOrderGateway()
    for symbol in ("BTC/USDT?x=1", "../BTCUSDT", "BTC USDT", "BTC/USDT/ETH"):
        result = gateway.validate(
            symbol=symbol, market=MarketType.SPOT, side="buy",
            order_type="limit", quantity=0.001, price=10, reference_price=10,
        )
        assert result.ok is False
        assert result.reason == "invalid_symbol"


def test_gateway_rejects_missing_reference_price_and_invalid_text_types():
    gateway = PreTradeOrderGateway()
    missing = gateway.validate(
        symbol="BTC/USDT", market=MarketType.FUTURES, side="buy",
        order_type="market", quantity=0.01,
    )
    assert missing.ok is False
    assert missing.reason == "reference_price_required_for_pretrade"
    malformed = gateway.validate(
        symbol=None, market=MarketType.SPOT, side="buy",
        order_type="market", quantity=0.01, reference_price=100,
    )
    assert malformed.ok is False
    assert malformed.reason == "invalid_order_text_fields"


def test_gateway_rejects_malformed_idempotency_permissions_and_exchange_filter_types():
    gateway = PreTradeOrderGateway()
    base = dict(
        symbol="BTC/USDT", market=MarketType.SPOT, side="buy",
        order_type="limit", quantity=0.001, price=10, reference_price=10,
    )
    assert gateway.validate(**base, idempotency_key=123).reason == "invalid_idempotency_key"
    assert gateway.validate(**base, api_permissions="trade").reason == "invalid_api_permissions"
    assert gateway.validate(**base, exchange_filters=[]).reason == "invalid_exchange_filters"
    assert gateway.validate(**base, exchange_filters={"tickSize": True}).reason == "invalid_exchange_filters"


def test_gateway_enforces_spot_100_usdt_per_trade_cap():
    result = PreTradeOrderGateway().validate(
        symbol="BTC/USDT", market=MarketType.SPOT, side="buy",
        order_type="market", quantity=0.003, reference_price=50000,
    )
    assert result.ok is False
    assert result.reason == "notional_exceeds_limit"
    assert result.normalized["max_notional"] == 100.0


def test_gateway_enforces_futures_800_usdt_total_notional_cap():
    result = PreTradeOrderGateway().validate(
        symbol="BTC/USDT", market=MarketType.FUTURES, side="buy",
        order_type="market", quantity=0.02, reference_price=50000,
        leverage=10,
    )
    assert result.ok is False
    assert result.reason == "notional_exceeds_limit"
    assert result.normalized["max_notional"] == 800.0


def test_live_pretrade_requires_strong_idempotency_key_and_boolean_flag():
    gateway = PreTradeOrderGateway()
    base = dict(
        symbol="BTC/USDT", market=MarketType.SPOT, side="buy",
        order_type="limit", quantity=0.001, price=10, reference_price=10,
        live_enabled=True,
    )
    assert gateway.validate(**base).reason == "live_order_idempotency_key_required"
    assert gateway.validate(**base, idempotency_key="short").reason == "live_order_idempotency_key_required"
    assert gateway.validate(**base, idempotency_key="stable-order-001").reason != "live_order_idempotency_key_required"
    assert gateway.validate(**{**base, "live_enabled": "false"}).reason == "invalid_live_enabled_flag"


def test_gateway_allows_futures_reduce_only_exit_above_entry_notional_cap():
    gateway = PreTradeOrderGateway()
    result = gateway.validate(
        symbol="BTC/USDT", market=MarketType.FUTURES, side="sell",
        order_type="market", quantity=0.02, reference_price=50000,
        leverage=10, reduce_only=True,
    )
    assert result.ok is True
    assert result.normalized["notional_estimate"] == 1000.0
    assert result.normalized["notional_cap_bypassed_for_reduce_only"] is True


def test_gateway_restricts_reduce_only_to_futures_closing_sides():
    gateway = PreTradeOrderGateway()
    spot = gateway.validate(
        symbol="BTC/USDT", market=MarketType.SPOT, side="sell",
        order_type="market", quantity=0.001, reference_price=50000,
        reduce_only=True,
    )
    invalid_side = gateway.validate(
        symbol="BTC/USDT", market=MarketType.FUTURES, side="long",
        order_type="market", quantity=0.001, reference_price=50000,
        reduce_only=True,
    )
    assert spot.reason == "reduce_only_futures_only"
    assert invalid_side.reason == "reduce_only_requires_buy_or_sell_side"


def test_explicit_notional_cap_still_applies_to_reduce_only_exit():
    result = PreTradeOrderGateway().validate(
        symbol="BTC/USDT", market=MarketType.FUTURES, side="sell",
        order_type="market", quantity=0.02, reference_price=50000,
        leverage=10, reduce_only=True, max_notional=750.0,
    )
    assert result.ok is False
    assert result.reason == "notional_exceeds_limit"


def test_gateway_allows_only_verified_spot_position_close_above_per_trade_cap():
    gateway = PreTradeOrderGateway()
    base = dict(
        symbol="BTC/USDT", market=MarketType.SPOT, side="sell",
        order_type="market", quantity=0.003, reference_price=50000,
    )
    unverified = gateway.validate(**base)
    verified = gateway.validate(**base, verified_position_close=True)
    assert unverified.reason == "notional_exceeds_limit"
    assert verified.ok is True
    assert verified.normalized["notional_cap_bypassed_for_verified_spot_close"] is True


def test_gateway_rejects_verified_spot_close_flag_for_non_spot_or_non_sell():
    gateway = PreTradeOrderGateway()
    futures = gateway.validate(
        symbol="BTC/USDT", market=MarketType.FUTURES, side="sell",
        order_type="market", quantity=0.001, reference_price=50000,
        verified_position_close=True,
    )
    spot_buy = gateway.validate(
        symbol="BTC/USDT", market=MarketType.SPOT, side="buy",
        order_type="market", quantity=0.001, reference_price=50000,
        verified_position_close=True,
    )
    assert futures.reason == "verified_position_close_spot_sell_only"
    assert spot_buy.reason == "verified_position_close_spot_sell_only"
