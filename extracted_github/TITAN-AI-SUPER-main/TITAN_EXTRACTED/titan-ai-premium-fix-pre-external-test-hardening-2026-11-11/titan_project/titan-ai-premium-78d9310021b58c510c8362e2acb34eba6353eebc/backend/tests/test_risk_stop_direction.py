from app.core.enums import EnvironmentMode, MarketType
from app.engines.risk import RiskEngine


def _validate(*, market=MarketType.SPOT, side="long", stop_loss=95.0):
    return RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO,
        market=market,
        balance=1000.0,
        risk_per_trade=0.01,
        entry=100.0,
        stop_loss=stop_loss,
        leverage=1.0,
        position_side=side,
    )


def test_long_position_rejects_stop_loss_at_or_above_entry():
    result = _validate(side="long", stop_loss=100.0)
    assert result["ok"] is False
    assert "stop_loss_wrong_side_for_position" in result["issues"]
    assert result["position_size"] == 0.0


def test_long_position_accepts_stop_loss_below_entry():
    result = _validate(side="long", stop_loss=95.0)
    assert result["ok"] is True


def test_short_position_rejects_stop_loss_at_or_below_entry():
    result = _validate(market=MarketType.FUTURES, side="short", stop_loss=100.0)
    assert result["ok"] is False
    assert "stop_loss_wrong_side_for_position" in result["issues"]
    assert result["position_size"] == 0.0


def test_short_position_accepts_stop_loss_above_entry():
    result = _validate(market=MarketType.FUTURES, side="short", stop_loss=105.0)
    assert result["ok"] is True


def test_invalid_position_side_fails_closed():
    result = _validate(side="sideways", stop_loss=95.0)
    assert result["ok"] is False
    assert "invalid_position_side" in result["issues"]
