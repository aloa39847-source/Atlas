import pytest

from app.engines.cross_margin import (
    maintenance_requirement,
    normalize_maintenance_brackets,
    simulate_cross_margin_account,
)


TIERS = [
    {"notionalFloor": 0, "notionalCap": 50_000, "maintMarginRatio": 0.004, "cum": 0},
    {"notionalFloor": 50_000, "notionalCap": 250_000, "maintMarginRatio": 0.01, "cum": 300},
]


def test_maintenance_bracket_applies_rate_and_cumulative_deduction():
    result = maintenance_requirement(60_000, TIERS)
    assert result["maintenance_margin"] == pytest.approx(300)
    assert result["maintenance_rate"] == pytest.approx(0.01)


def test_cross_margin_aggregates_unrealized_pnl_and_maintenance_across_positions():
    result = simulate_cross_margin_account(
        wallet_balance=2_000,
        positions=[
            {"symbol": "BTCUSDT", "side": "long", "quantity": 0.2,
             "entry_price": 50_000, "mark_price": 51_000, "leverage": 5,
             "maintenance_brackets": TIERS},
            {"symbol": "ETHUSDT", "side": "short", "quantity": 1,
             "entry_price": 3_000, "mark_price": 3_100, "leverage": 3,
             "maintenance_brackets": TIERS},
        ],
    )
    assert result["ok"] is True
    assert result["margin_mode"] == "cross"
    assert result["cross_equity"] == pytest.approx(2_100)
    assert result["maintenance_margin"] == pytest.approx(53.2)
    assert result["liquidation_breach"] is False
    assert result["no_live_orders"] is True


def test_cross_margin_detects_shared_wallet_equity_exhaustion():
    result = simulate_cross_margin_account(
        wallet_balance=100,
        positions=[{"symbol": "BTCUSDT", "side": "long", "quantity": 1,
                    "entry_price": 1000, "mark_price": 900, "leverage": 10,
                    "maintenance_brackets": TIERS}],
    )
    assert result["ok"] is True
    assert result["cross_equity"] == 0
    assert result["liquidation_breach"] is True


def test_cross_margin_rejects_missing_or_incomplete_tier_data_instead_of_guessing():
    missing = simulate_cross_margin_account(
        wallet_balance=1000,
        positions=[{"symbol": "BTCUSDT", "side": "long", "quantity": 0.1,
                    "entry_price": 100, "mark_price": 100, "leverage": 2}],
    )
    assert missing["ok"] is False
    assert "maintenance_brackets_missing" in missing["error"]

    with pytest.raises(ValueError, match="gap_or_overlap"):
        normalize_maintenance_brackets([
            {"notionalFloor": 0, "notionalCap": 10, "maintMarginRatio": 0.01, "cum": 0},
            {"notionalFloor": 11, "notionalCap": 20, "maintMarginRatio": 0.02, "cum": 0},
        ])


def test_cross_margin_rejects_bad_inputs_and_short_pnl_is_signed_correctly():
    result = simulate_cross_margin_account(
        wallet_balance=1000,
        positions=[{"symbol": "ETHUSDT", "side": "short", "quantity": 2,
                    "entry_price": 100, "mark_price": 90, "leverage": 2,
                    "maintenance_brackets": TIERS}],
    )
    assert result["positions"][0]["unrealized_pnl"] == pytest.approx(20)
    invalid = simulate_cross_margin_account(wallet_balance=1000, positions=[])
    assert invalid["ok"] is False
