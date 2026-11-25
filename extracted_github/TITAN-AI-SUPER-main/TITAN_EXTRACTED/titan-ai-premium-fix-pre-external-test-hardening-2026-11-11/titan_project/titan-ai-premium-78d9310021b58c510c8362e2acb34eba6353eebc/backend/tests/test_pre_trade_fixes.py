"""Tests for 4 pre-trade fixes - simulation/mock only, no real orders."""
from __future__ import annotations

from app.core.enums import EnvironmentMode, MarketType
from app.engines.order_gateway import PreTradeOrderGateway
from app.services.position_manager import ManagedPosition, PositionRiskManager


def test_filter_tick_size_rejects_misaligned_price():
    g = PreTradeOrderGateway()
    res = g.validate(symbol='BTCUSDT', market=MarketType.SPOT, side='buy', order_type='limit',
                     quantity=0.01, price=50000.03, reference_price=50000,
                     environment_mode='demo', exchange_filters={'tickSize': 0.1, 'stepSize': 0.01, 'minNotional': 5})
    assert not res.ok
    assert res.reason == 'price_not_aligned_to_tick_size'


def test_filter_step_size_rejects_misaligned_qty():
    g = PreTradeOrderGateway()
    res = g.validate(symbol='BTCUSDT', market=MarketType.SPOT, side='buy', order_type='limit',
                     quantity=0.0015, price=50000.0, reference_price=50000,
                     environment_mode='demo', exchange_filters={'tickSize': 0.1, 'stepSize': 0.01, 'minNotional': 5})
    assert not res.ok
    assert res.reason == 'quantity_not_aligned_to_step_size'


def test_filter_min_notional_rejects():
    g = PreTradeOrderGateway()
    res = g.validate(symbol='BTCUSDT', market=MarketType.SPOT, side='buy', order_type='limit',
                     quantity=0.0001, price=50000.0, reference_price=50000,
                     environment_mode='demo', exchange_filters={'tickSize': 0.1, 'stepSize': 0.00001, 'minNotional': 10})
    assert not res.ok
    assert res.reason == 'below_min_notional'


def test_filter_accepts_valid():
    g = PreTradeOrderGateway()
    res = g.validate(symbol='BTCUSDT', market=MarketType.SPOT, side='buy', order_type='limit',
                     quantity=0.001, price=50000.0, reference_price=50000,
                     environment_mode='demo', exchange_filters={'tickSize': 0.1, 'stepSize': 0.0001, 'minNotional': 5})
    assert res.ok
    assert res.normalized['exchange_filters_checked'] is True


def test_sl_tp_trailing_simulation():
    pos = ManagedPosition(symbol='BTCUSDT', side='long', entry_price=100, quantity=1, stop_loss=95, take_profit=110, trailing_stop_pct=0.05)
    assert pos.update_price(102)['action'] is None
    # hit trailing after rise
    pos.update_price(120)
    r = pos.update_price(113)  # below trailing
    assert r['action'] == 'close'
    assert r['reason'] in ('trailing_stop','stop_loss','take_profit')




def test_reconcile_structure():
    # mock reconcile logic
    from app.integrations.binance_adapter import BinanceTestnetAdapter, BinanceConfig
    b = BinanceTestnetAdapter(BinanceConfig())
    # simulate by calling method exists
    assert hasattr(b, 'reconcile')



def test_position_manager_rejects_non_finite_prices_without_mutating_state():
    import math

    position = ManagedPosition(
        symbol="BTCUSDT", side="long", entry_price=100, quantity=1,
        stop_loss=95, take_profit=110, trailing_stop_pct=0.02,
    )
    result = position.update_price(math.nan)
    assert result["action"] is None
    assert result["reason"] == "invalid_position_or_price"
    assert position.highest_price is None


def test_position_manager_rejects_unknown_side_instead_of_treating_it_as_short():
    position = ManagedPosition(
        symbol="BTCUSDT", side="unknown", entry_price=100, quantity=1,
        stop_loss=105, take_profit=90,
    )
    result = position.update_price(110)
    assert result["action"] is None
    assert result["reason"] == "invalid_position_side"
    assert position.lowest_price is None


def test_position_manager_rejects_invalid_trailing_percentage():
    position = ManagedPosition(
        symbol="BTCUSDT", side="long", entry_price=100, quantity=1,
        trailing_stop_pct=1.5,
    )
    result = position.update_price(110)
    assert result["action"] is None
    assert result["reason"] == "invalid_trailing_stop_pct"



def test_position_manager_rejects_malformed_protection_price_without_raising():
    position = ManagedPosition(
        symbol="BTCUSDT", side="long", entry_price=100, quantity=1,
        stop_loss="not-a-price",
    )
    result = position.update_price(90)
    assert result["action"] is None
    assert result["reason"] == "invalid_position_or_price"
    assert position.stop_loss == "not-a-price"
