from app.core.enums import MarketType
from app.engines.order_gateway import PreTradeOrderGateway
from app.integrations.binance_adapter import BinanceConfig, BinanceTestnetAdapter


def _gateway(**kwargs):
    base = dict(
        symbol="BTC/USDT", market=MarketType.SPOT, side="buy",
        order_type="limit", quantity=0.01, price=100, reference_price=100,
        exchange_filters={
            "tickSize": "0.01", "stepSize": "0.001",
            "minQty": "0.001", "maxQty": "10",
            "minPrice": "1", "maxPrice": "100000",
            "minNotional": "5", "maxNotional": "10000",
        },
    )
    base.update(kwargs)
    return PreTradeOrderGateway().validate(**base)


def test_gateway_enforces_min_max_quantity_and_price():
    assert _gateway(quantity=0.0001).reason == "quantity_below_min_qty"
    assert _gateway(quantity=11).reason == "quantity_above_max_qty"
    assert _gateway(price=0.99, reference_price=1).reason == "price_below_min_price"
    assert _gateway(price=100001, reference_price=100000).reason == "price_above_max_price"


def test_gateway_enforces_market_specific_quantity_step_and_bounds():
    result = _gateway(
        order_type="market", price=None, quantity=0.0001,
        reference_price=100,
        exchange_filters={
            "stepSize": "0.001", "marketStepSize": "0.01",
            "minQty": "0.001", "maxQty": "100",
            "marketMinQty": "0.01", "marketMaxQty": "20",
        },
    )
    assert result.reason == "quantity_below_min_qty"
    result = _gateway(
        order_type="market", price=None, quantity=0.015,
        reference_price=100,
        exchange_filters={"stepSize":"0.001","marketStepSize":"0.01",
                          "marketMinQty":"0.01","marketMaxQty":"20"},
    )
    assert result.reason == "quantity_not_aligned_to_step_size"


def test_gateway_enforces_max_notional_and_market_apply_flags():
    assert _gateway(quantity=0.5, exchange_filters={"stepSize":"0.001","minQty":"0.001","maxQty":"10","maxNotional":"40"}).reason == "above_max_notional"
    # Binance can disable MIN_NOTIONAL enforcement for market orders.
    result = _gateway(
        order_type="market", price=None, quantity=0.001, reference_price=10,
        exchange_filters={"stepSize":"0.001","minNotional":"5","applyMinToMarket":False},
    )
    assert result.ok is True


def test_gateway_enforces_percent_price_band_for_limit_orders():
    result = _gateway(
        price=115, reference_price=100,
        exchange_filters={"tickSize":"0.01","stepSize":"0.001",
                          "percentMultiplierUp":"1.1","percentMultiplierDown":"0.8"},
    )
    assert result.reason == "price_outside_percent_price_filter"


def test_gateway_rejects_invalid_negative_filter_values():
    result = _gateway(exchange_filters={"tickSize":"-0.01","stepSize":"0.001"})
    assert result.ok is False
    assert result.reason == "invalid_exchange_filters"


def test_binance_live_api_restrictions_require_ip_allowlist_and_market_trade_permission():
    spot = BinanceTestnetAdapter(BinanceConfig(environment="live", market="spot"))
    valid = {"ipRestrict": True, "ipList": ["203.0.113.10"],
             "enableSpotAndMarginTrading": True, "enableWithdrawals": False,
             "enableInternalTransfer": False, "permitsUniversalTransfer": False, "enableUniversalTransfer": False}
    assert spot._validate_live_api_restrictions(valid) is None
    # Binance documents ipRestrict but does not guarantee returning ipList.
    assert spot._validate_live_api_restrictions({k:v for k,v in valid.items() if k != "ipList"}) is None
    assert spot._validate_live_api_restrictions({**valid, "ipRestrict": False}) == "live_api_ip_allowlist_required"
    assert spot._validate_live_api_restrictions({**valid, "ipList": []}) == "live_api_ip_allowlist_empty_or_invalid"
    assert spot._validate_live_api_restrictions({**valid, "enableWithdrawals": True}) == "live_api_key_has_withdrawal_or_transfer_permissions"
    assert spot._validate_live_api_restrictions({**valid, "permitsUniversalTransfer": True}) == "live_api_key_has_withdrawal_or_transfer_permissions"
    assert spot._validate_live_api_restrictions({**valid, "enableSpotAndMarginTrading": False}) == "live_api_required_market_permission_missing"


def test_binance_futures_requires_futures_permission_not_spot_permission():
    futures = BinanceTestnetAdapter(BinanceConfig(environment="live", market="futures"))
    valid = {"ipRestrict": True, "ipList": ["203.0.113.10"],
             "enableFutures": True, "enableWithdrawals": False,
             "enableInternalTransfer": False, "enableUniversalTransfer": False}
    assert futures._validate_live_api_restrictions(valid) is None
    assert futures._validate_live_api_restrictions({**valid, "enableFutures": False}) == "live_api_required_market_permission_missing"


def test_gateway_enforces_binance_minimum_offset_for_quantity_and_price():
    valid = _gateway(quantity=0.15, price=0.15, reference_price=0.15,
                     exchange_filters={"minQty":"0.05","maxQty":"10","stepSize":"0.10",
                                       "minPrice":"0.05","maxPrice":"100","tickSize":"0.10"})
    assert valid.ok is True
    bad_qty = _gateway(quantity=0.10, price=0.15, reference_price=0.15,
                       exchange_filters={"minQty":"0.05","stepSize":"0.10","tickSize":"0.01"})
    assert bad_qty.reason == "quantity_not_aligned_to_step_size"
    bad_price = _gateway(quantity=0.01, price=0.10, reference_price=0.10,
                         exchange_filters={"stepSize":"0.001","minPrice":"0.05","tickSize":"0.10"})
    assert bad_price.reason == "price_not_aligned_to_tick_size"


def test_gateway_enforces_max_position_and_open_order_limits():
    base = dict(symbol="BTC/USDT", market=MarketType.SPOT, side="buy",
                order_type="limit", quantity=0.5, price=10, reference_price=10,
                exchange_filters={"stepSize":"0.1","maxPosition":"1",
                                  "maxNumOrders":"3","exchangeMaxNumOrders":"10"})
    assert PreTradeOrderGateway().validate(**base, current_position=0.6,
        current_open_orders_count=1, current_exchange_open_orders_count=2).reason == "max_position_exceeded"
    assert PreTradeOrderGateway().validate(**base, current_position=0.1,
        current_open_orders_count=3, current_exchange_open_orders_count=2).reason == "max_num_orders_exceeded"
    assert PreTradeOrderGateway().validate(**base, current_position=0.1,
        current_open_orders_count=1, current_exchange_open_orders_count=10).reason == "exchange_max_num_orders_exceeded"


def test_adapter_blocks_self_trade_and_same_side_limit_layering():
    from app.integrations.binance_adapter import BinanceTestnetAdapter
    veto = BinanceTestnetAdapter._open_order_conduct_veto
    assert veto("BUY", "LIMIT", 100, [{"side":"BUY","price":"99"}]) == "repeated_same_side_limit_order_blocked"
    assert veto("BUY", "LIMIT", 101, [{"side":"SELL","price":"100"}]) == "potential_self_trade_with_crossing_open_order"
    assert veto("SELL", "LIMIT", 99, [{"side":"BUY","price":"100"}]) == "potential_self_trade_with_crossing_open_order"
    assert veto("BUY", "MARKET", None, [{"side":"SELL","price":"500"}]) == "potential_self_trade_with_opposite_open_order"
    assert veto("BUY", "LIMIT", 99, [{"side":"SELL","price":"100"}]) is None


def test_adapter_reads_position_order_and_weighted_average_filters(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(environment="testnet", market="spot"))
    monkeypatch.setattr(adapter, "get_exchange_info", lambda symbol: {
        "symbols": [{
            "symbol": "BTCUSDT", "status": "TRADING",
            "filters": [
                {"filterType":"PRICE_FILTER","minPrice":"0.01","maxPrice":"1000000","tickSize":"0.01"},
                {"filterType":"LOT_SIZE","minQty":"0.001","maxQty":"100","stepSize":"0.001"},
                {"filterType":"MAX_POSITION","maxPosition":"5"},
                {"filterType":"PERCENT_PRICE","multiplierUp":"1.2","multiplierDown":"0.8","avgPriceMins":5},
                {"filterType":"MAX_NUM_ORDERS","maxNumOrders":200},
            ],
        }],
        "exchangeFilters": [{"filterType":"EXCHANGE_MAX_NUM_ORDERS","maxNumOrders":1000}],
    })
    filters = adapter._symbol_filters("BTCUSDT")
    assert filters["maxPosition"] == 5
    assert filters["maxNumOrders"] == 200
    assert filters["exchangeMaxNumOrders"] == 1000
    assert filters["avgPriceMins"] == 5
