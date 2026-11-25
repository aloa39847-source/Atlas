from app.core.config import settings
from app.core.enums import EnvironmentMode, MarketType
from app.engines.risk import RiskEngine


def test_user_spot_trade_notional_is_capped_at_100_usdt():
    result = RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO,
        market=MarketType.SPOT,
        balance=1_000.0,
        risk_per_trade=0.01,
        entry=100.0,
        stop_loss=99.0,
        leverage=1.0,
    )

    assert result["ok"] is True
    assert result["position_notional_estimate"] == 100.0
    assert result["quantity_estimate"] == 1.0
    assert "per_trade_notional_cap_applied:100.00" in result["issues"]


def test_user_futures_trade_notional_is_capped_at_800_usdt():
    result = RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO,
        market=MarketType.FUTURES,
        balance=1_000.0,
        risk_per_trade=0.01,
        entry=100.0,
        stop_loss=99.0,
        leverage=5.0,
    )

    assert result["ok"] is True
    assert result["position_notional_estimate"] == 800.0
    assert result["quantity_estimate"] == 8.0
    assert "per_trade_notional_cap_applied:800.00" in result["issues"]


def test_daily_loss_cap_default_is_seven_percent():
    assert settings.max_daily_loss_cap == 0.07


def test_daily_loss_limit_blocks_at_seven_percent():
    result = RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO,
        market=MarketType.FUTURES,
        balance=1_000.0,
        risk_per_trade=0.01,
        entry=100.0,
        stop_loss=99.0,
        leverage=2.0,
        daily_loss_used=70.0,
    )

    assert result["ok"] is False
    assert "daily_loss_limit_reached" in result["issues"]


def test_portfolio_wide_exposure_blocks_entry_even_when_single_trade_is_under_cap():
    result = RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO,
        market=MarketType.SPOT,
        balance=1_000.0,
        risk_per_trade=0.01,
        entry=100.0,
        stop_loss=99.0,
        leverage=1.0,
        current_exposure=950.0,
        max_exposure=1_000.0,
    )
    assert result["ok"] is False
    assert "max_exposure_exceeded" in result["issues"]
    assert result["projected_exposure"] == 1_050.0


def test_portfolio_open_position_count_is_enforced():
    result = RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO,
        market=MarketType.FUTURES,
        balance=1_000.0,
        risk_per_trade=0.01,
        entry=100.0,
        stop_loss=99.0,
        leverage=2.0,
        open_positions=10,
        max_open_positions=10,
    )
    assert result["ok"] is False
    assert "max_open_positions_reached" in result["issues"]


def test_daily_loss_limit_allows_new_trade_just_below_seven_percent():
    result = RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO,
        market=MarketType.SPOT,
        balance=1_000.0,
        risk_per_trade=0.01,
        entry=100.0,
        stop_loss=99.0,
        leverage=1.0,
        daily_loss_used=69.99,
    )
    assert result["ok"] is True
    assert "daily_loss_limit_reached" not in result["issues"]


def test_explicit_daily_loss_override_cannot_relax_global_seven_percent_cap():
    result = RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO,
        market=MarketType.SPOT,
        balance=1_000.0,
        risk_per_trade=0.01,
        entry=100.0,
        stop_loss=99.0,
        leverage=1.0,
        daily_loss_used=70.0,
        max_daily_loss=0.10,
    )
    assert result["ok"] is False
    assert "daily_loss_limit_reached" in result["issues"]


def test_explicit_stricter_daily_loss_override_is_preserved():
    result = RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO,
        market=MarketType.SPOT,
        balance=1_000.0,
        risk_per_trade=0.01,
        entry=100.0,
        stop_loss=99.0,
        leverage=1.0,
        daily_loss_used=50.0,
        max_daily_loss=0.05,
    )
    assert result["ok"] is False
    assert "daily_loss_limit_reached" in result["issues"]


def test_invalid_global_daily_loss_policy_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "max_daily_loss_cap", 0.0)
    result = RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO,
        market=MarketType.SPOT,
        balance=1_000.0,
        risk_per_trade=0.01,
        entry=100.0,
        stop_loss=99.0,
        leverage=1.0,
    )
    assert result["ok"] is False
    assert "invalid_max_daily_loss" in result["issues"]
