from pathlib import Path
import sys

root = Path(sys.argv[1])
risk_path = root / "app/engines/risk.py"
risk_path.write_text(r'''from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

from app.core.config import settings
from app.core.enums import EngineStatus, EnvironmentMode, MarketType
from .base import Engine


@dataclass
class CostEstimate:
    maker_fee_rate: float
    taker_fee_rate: float
    estimated_fees: float
    estimated_slippage: float
    funding_fee_estimate: float
    total_cost_estimate: float
    assumptions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "maker_fee_rate": self.maker_fee_rate,
            "taker_fee_rate": self.taker_fee_rate,
            "estimated_fees": round(self.estimated_fees, 8),
            "estimated_slippage": round(self.estimated_slippage, 8),
            "funding_fee_estimate": round(self.funding_fee_estimate, 8),
            "total_cost_estimate": round(self.total_cost_estimate, 8),
            "assumptions": self.assumptions,
            "is_estimate": True,
        }


def _finite_number(name: str, value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name}_must_be_finite_number") from exc
    if not isfinite(result):
        raise ValueError(f"{name}_must_be_finite_number")
    return result


class RiskEngine(Engine):
    key = "risk_management"
    name = "Risk Management Engine"

    def status(self) -> EngineStatus:
        return EngineStatus.IMPLEMENTED_PARTIAL

    def estimate_costs(
        self,
        notional: float,
        market: MarketType,
        maker_taker: str = "taker",
        maker_fee_rate: float = 0.001,
        taker_fee_rate: float = 0.001,
        slippage_rate: float = 0.0005,
        funding_rate: float = 0.0,
        funding_periods: float = 0.0,
    ) -> CostEstimate:
        notional = _finite_number("notional", notional)
        maker_fee_rate = _finite_number("maker_fee_rate", maker_fee_rate)
        taker_fee_rate = _finite_number("taker_fee_rate", taker_fee_rate)
        slippage_rate = _finite_number("slippage_rate", slippage_rate)
        funding_rate = _finite_number("funding_rate", funding_rate)
        funding_periods = _finite_number("funding_periods", funding_periods)
        if notional < 0:
            raise ValueError("notional_must_be_non_negative")
        if any(rate < 0 for rate in (maker_fee_rate, taker_fee_rate, slippage_rate)):
            raise ValueError("fee_and_slippage_rates_must_be_non_negative")
        if funding_periods < 0:
            raise ValueError("funding_periods_must_be_non_negative")
        if maker_taker not in {"maker", "taker"}:
            raise ValueError("maker_taker_must_be_maker_or_taker")

        assumptions = [
            "Fee/slippage/funding values are CONFIGURABLE ESTIMATES; verify actual Binance fees, tier, BNB discount, and market conditions before live use.",
        ]
        fee_rate = maker_fee_rate if maker_taker == "maker" else taker_fee_rate
        fees = notional * fee_rate * 2  # entry + exit estimate
        slip = notional * slippage_rate
        funding = notional * abs(funding_rate) * funding_periods if market == MarketType.FUTURES else 0.0
        if market == MarketType.FUTURES and funding > 0:
            assumptions.append("Funding estimate included for futures; actual funding varies over time.")
        if market == MarketType.EARN:
            assumptions.append("Earn products have product-specific terms/yields/lockups; not modeled as trade execution here.")
        return CostEstimate(maker_fee_rate, taker_fee_rate, fees, slip, funding, fees + slip + funding, assumptions)

    def validate_trade(
        self,
        *,
        environment: EnvironmentMode,
        market: MarketType,
        balance: float,
        risk_per_trade: float,
        entry: float,
        stop_loss: float,
        leverage: float,
        max_leverage: float | None = None,
        max_risk_per_trade: float | None = None,
        daily_loss_used: float = 0.0,
        max_daily_loss: float | None = None,
        open_positions: int = 0,
        max_open_positions: int | None = None,
        current_exposure: float = 0.0,
        max_exposure: float | None = None,
    ) -> dict[str, Any]:
        issues: list[str] = []
        try:
            balance = _finite_number("balance", balance)
            risk_per_trade = _finite_number("risk_per_trade", risk_per_trade)
            entry = _finite_number("entry", entry)
            stop_loss = _finite_number("stop_loss", stop_loss)
            leverage = _finite_number("leverage", leverage)
            daily_loss_used = _finite_number("daily_loss_used", daily_loss_used)
            current_exposure = _finite_number("current_exposure", current_exposure)
            requested_max_leverage = _finite_number(
                "max_leverage", settings.max_leverage_cap if max_leverage is None else max_leverage
            )
            requested_max_risk = _finite_number(
                "max_risk_per_trade",
                settings.max_risk_per_trade_cap if max_risk_per_trade is None else max_risk_per_trade,
            )
            max_daily = _finite_number(
                "max_daily_loss", settings.max_daily_loss_cap if max_daily_loss is None else max_daily_loss
            )
            max_exp = _finite_number("max_exposure", balance * 2.0 if max_exposure is None else max_exposure)
            max_pos_value = settings.max_open_positions_cap if max_open_positions is None else max_open_positions
            max_pos_float = _finite_number("max_open_positions", max_pos_value)
            open_positions_float = _finite_number("open_positions", open_positions)
        except ValueError as exc:
            return {"ok": False, "issues": [str(exc)], "position_size": 0.0}

        if balance <= 0 or entry <= 0 or stop_loss <= 0:
            return {"ok": False, "issues": ["invalid_balance_or_prices"], "position_size": 0.0}
        if risk_per_trade <= 0:
            return {"ok": False, "issues": ["risk_per_trade_must_be_positive"], "position_size": 0.0}
        if leverage <= 0:
            return {"ok": False, "issues": ["leverage_must_be_positive"], "position_size": 0.0}
        if requested_max_leverage <= 0:
            return {"ok": False, "issues": ["max_leverage_must_be_positive"], "position_size": 0.0}
        if requested_max_risk <= 0:
            return {"ok": False, "issues": ["max_risk_per_trade_must_be_positive"], "position_size": 0.0}
        if daily_loss_used < 0 or current_exposure < 0:
            return {"ok": False, "issues": ["daily_loss_and_exposure_must_be_non_negative"], "position_size": 0.0}
        if max_daily < 0 or max_exp <= 0:
            return {"ok": False, "issues": ["portfolio_limits_out_of_range"], "position_size": 0.0}
        if max_pos_float < 1 or not max_pos_float.is_integer() or open_positions_float < 0 or not open_positions_float.is_integer():
            return {"ok": False, "issues": ["position_counts_out_of_range"], "position_size": 0.0}
        open_positions = int(open_positions_float)
        max_pos = int(max_pos_float)

        max_lev = min(requested_max_leverage, settings.max_leverage_cap)
        max_risk = min(requested_max_risk, settings.max_risk_per_trade_cap)
        lev = leverage
        if market == MarketType.SPOT:
            lev = 1.0
        if market == MarketType.EARN:
            issues.append("earn_is_not_margin_trade_execution")
            lev = 1.0
        if lev < 1:
            lev = 1.0
        if lev > max_lev:
            issues.append(f"leverage_capped:{lev}->{max_lev}")
            lev = max_lev
        rpt = min(risk_per_trade, max_risk)
        stop_dist = abs(entry - stop_loss) / entry
        if stop_dist <= 0:
            issues.append("stop_distance_zero")
            return {"ok": False, "issues": issues, "position_size": 0.0}
        risk_amount = balance * rpt
        hard_position_cap = 80.0 if market == MarketType.SPOT else 800.0 if market == MarketType.FUTURES else 0.0
        if hard_position_cap <= 0:
            issues.append("unsupported_market_for_trade_sizing")
            return {"ok": False, "issues": issues, "position_size": 0.0}
        requested_notional = min((risk_amount / stop_dist), balance * lev)
        position_notional = min(requested_notional, hard_position_cap)
        if requested_notional > hard_position_cap:
            issues.append(f"per_position_notional_capped:{hard_position_cap}")
        qty = position_notional / entry
        if daily_loss_used >= balance * max_daily:
            issues.append("daily_loss_limit_reached")
        if open_positions >= max_pos:
            issues.append("max_open_positions_reached")
        if current_exposure + position_notional > max_exp:
            issues.append("max_exposure_exceeded")
        liq_note = None
        if market == MarketType.FUTURES and lev > 1:
            approx_liq_dist = (1 / lev) * 0.9
            liq_note = f"Simplified liquidation-distance estimate ~{approx_liq_dist*100:.2f}% (NOT exchange-accurate)."
        hard_block = any(i.startswith("invalid") or i == "earn_is_not_margin_trade_execution" or i in ("daily_loss_limit_reached","max_open_positions_reached","max_exposure_exceeded") for i in issues)
        return {
            "ok": not hard_block,
            "issues": issues,
            "environment": environment.value,
            "market": market.value,
            "leverage_used": lev,
            "risk_per_trade_used": rpt,
            "risk_amount": round(risk_amount, 8),
            "stop_distance_pct": round(stop_dist * 100, 6),
            "position_notional_estimate": round(position_notional, 8),
            "quantity_estimate": round(qty, 8),
            "liquidation_note": liq_note,
            "note": "Estimates for planning/testing; verify exchange precision, margin, liquidation and fees before real execution.",
        }
''')

tests = r'''import math
import pytest

from app.core.enums import EnvironmentMode, MarketType
from app.engines.risk import RiskEngine


def _valid(**overrides):
    values = dict(
        environment=EnvironmentMode.DEMO, market=MarketType.SPOT, balance=1000.0,
        risk_per_trade=0.01, entry=100.0, stop_loss=99.0, leverage=1.0,
    )
    values.update(overrides)
    return RiskEngine().validate_trade(**values)


@pytest.mark.parametrize("field,value", [
    ("balance", math.nan), ("entry", math.inf), ("stop_loss", -math.inf),
    ("risk_per_trade", math.nan), ("leverage", math.inf),
    ("daily_loss_used", math.nan), ("current_exposure", math.inf),
    ("max_exposure", math.nan), ("max_daily_loss", math.inf),
    ("max_leverage", math.nan), ("max_risk_per_trade", math.inf),
    ("open_positions", math.nan), ("max_open_positions", math.inf),
])
def test_trade_validation_fails_closed_for_non_finite_inputs(field, value):
    result = _valid(**{field: value})
    assert result["ok"] is False
    assert result["position_size"] == 0.0
    assert result["issues"][0].endswith("_must_be_finite_number")


@pytest.mark.parametrize("field,value", [
    ("daily_loss_used", -1), ("current_exposure", -1),
    ("max_exposure", 0), ("max_open_positions", 0),
    ("open_positions", -1), ("leverage", 0),
])
def test_trade_validation_rejects_invalid_negative_or_zero_controls(field, value):
    result = _valid(**{field: value})
    assert result["ok"] is False
    assert result["position_size"] == 0.0


@pytest.mark.parametrize("kwargs", [
    {"notional": math.nan},
    {"notional": math.inf},
    {"maker_fee_rate": math.nan},
    {"taker_fee_rate": -0.001},
    {"slippage_rate": math.inf},
    {"funding_periods": -1},
    {"maker_taker": "unknown"},
])
def test_cost_estimation_rejects_invalid_inputs(kwargs):
    args = dict(notional=100.0, market=MarketType.FUTURES)
    args.update(kwargs)
    with pytest.raises(ValueError):
        RiskEngine().estimate_costs(**args)


def test_valid_trade_still_respects_user_approved_position_caps():
    spot = _valid(balance=10000, risk_per_trade=0.02, entry=100, stop_loss=99, leverage=1)
    futures = _valid(market=MarketType.FUTURES, balance=100000, risk_per_trade=0.02,
                     entry=100, stop_loss=99, leverage=3)
    assert spot["ok"] is True and spot["position_notional_estimate"] <= 80
    assert futures["ok"] is True and futures["position_notional_estimate"] <= 800
'''
(root / "tests/test_risk_input_validation.py").write_text(tests)
print("Risk input validation patch and tests applied.")
