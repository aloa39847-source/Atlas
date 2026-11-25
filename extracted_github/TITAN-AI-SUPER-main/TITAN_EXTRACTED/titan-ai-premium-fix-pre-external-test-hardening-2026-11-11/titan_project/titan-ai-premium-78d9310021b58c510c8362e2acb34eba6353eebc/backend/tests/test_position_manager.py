import pytest

from app.services.position_manager import ManagedPosition, PositionRiskManager


@pytest.mark.parametrize("field,value", [
    ("stop_loss", 0.0),
    ("take_profit", -1.0),
    ("trailing_stop_price", 0.0),
    ("highest_price", -1.0),
    ("lowest_price", 0.0),
])
def test_position_manager_rejects_non_positive_protection_prices(field, value):
    position = ManagedPosition(
        symbol="BTC/USDT", side="long", entry_price=100.0, quantity=0.1,
        **{field: value},
    )
    result = position.update_price(99.0)
    assert result["action"] is None
    assert result["reason"] == "invalid_position_or_price"


def test_position_manager_closes_long_at_zero_or_positive_valid_stop_level():
    position = ManagedPosition(
        symbol="BTC/USDT", side="long", entry_price=100.0,
        quantity=0.1, stop_loss=95.0,
    )
    result = position.update_price(95.0)
    assert result["action"] == "close"
    assert result["reason"] == "stop_loss"


def test_position_manager_closes_short_at_take_profit():
    position = ManagedPosition(
        symbol="BTC/USDT", side="short", entry_price=100.0,
        quantity=0.1, take_profit=90.0,
    )
    result = position.update_price(90.0)
    assert result["action"] == "close"
    assert result["reason"] == "take_profit"


def test_position_risk_manager_ignores_missing_marks_and_emits_valid_exit():
    positions = [
        ManagedPosition("BTC/USDT", "long", 100.0, 0.1, stop_loss=95.0),
        ManagedPosition("ETH/USDT", "long", 100.0, 0.1, stop_loss=95.0),
    ]
    result = PositionRiskManager().evaluate_positions(
        positions, {"BTC/USDT": 94.0},
    )
    assert len(result) == 1
    assert result[0]["symbol"] == "BTC/USDT"
    assert result[0]["reason"] == "stop_loss"


def test_long_trailing_stop_only_moves_up_and_closes_on_retrace():
    position = ManagedPosition(
        symbol="BTC/USDT", side="long", entry_price=100.0,
        quantity=0.1, trailing_stop_pct=0.02,
    )
    first = position.update_price(100.0)
    assert first["trailing_stop_price"] == 98.0
    second = position.update_price(110.0)
    assert second["trailing_stop_price"] == 107.8
    third = position.update_price(109.0)
    assert third["trailing_stop_price"] == 107.8
    final = position.update_price(107.8)
    assert final["action"] == "close"
    assert final["reason"] == "trailing_stop"


def test_short_trailing_stop_only_moves_down_and_closes_on_retrace():
    position = ManagedPosition(
        symbol="BTC/USDT", side="short", entry_price=100.0,
        quantity=0.1, trailing_stop_pct=0.02,
    )
    first = position.update_price(100.0)
    assert first["trailing_stop_price"] == 102.0
    second = position.update_price(90.0)
    assert second["trailing_stop_price"] == 91.8
    third = position.update_price(91.0)
    assert third["trailing_stop_price"] == 91.8
    final = position.update_price(91.8)
    assert final["action"] == "close"
    assert final["reason"] == "trailing_stop"


@pytest.mark.parametrize("side,trailing_pct", [
    ("long", 0.0),
    ("long", 0.21),
    ("short", -0.01),
    ("unknown", 0.02),
])
def test_position_manager_rejects_invalid_side_or_trailing_configuration(side, trailing_pct):
    position = ManagedPosition(
        symbol="BTC/USDT", side=side, entry_price=100.0,
        quantity=0.1, trailing_stop_pct=trailing_pct,
    )
    result = position.update_price(100.0)
    assert result["action"] is None
    assert result["reason"] in {"invalid_position_side", "invalid_trailing_stop_pct"}
