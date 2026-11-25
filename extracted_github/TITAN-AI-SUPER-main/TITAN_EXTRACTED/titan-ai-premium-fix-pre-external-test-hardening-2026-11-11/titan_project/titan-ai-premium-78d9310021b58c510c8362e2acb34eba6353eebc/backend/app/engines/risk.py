from __future__ import annotations

from dataclasses import dataclass
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
        assumptions = [
            "Fee/slippage/funding values are CONFIGURABLE ESTIMATES; verify actual Binance fees, tier, BNB discount, and market conditions before live use.",
        ]
        if not isinstance(market, MarketType):
            raise ValueError("invalid_market_type")
        if not isinstance(maker_taker, str) or maker_taker not in {"maker", "taker"}:
            raise ValueError("maker_taker_must_be_maker_or_taker")
        if any(isinstance(value, bool) for value in (notional, maker_fee_rate, taker_fee_rate, slippage_rate, funding_rate, funding_periods)):
            raise ValueError("boolean_cost_estimate_input_not_allowed")
        try:
            values = {
                "notional": float(notional), "maker_fee_rate": float(maker_fee_rate),
                "taker_fee_rate": float(taker_fee_rate), "slippage_rate": float(slippage_rate),
                "funding_rate": float(funding_rate), "funding_periods": float(funding_periods),
            }
        except (TypeError, ValueError, OverflowError):
            raise ValueError("invalid_cost_estimate_numeric_input")
        import math
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError("non_finite_cost_estimate_input")
        if (values["notional"] < 0 or not 0 <= values["maker_fee_rate"] < 1
                or not 0 <= values["taker_fee_rate"] < 1
                or not 0 <= values["slippage_rate"] < 1
                or values["funding_periods"] < 0):
            raise ValueError("cost_estimate_inputs_out_of_range")
        notional = values["notional"]
        maker_fee_rate = values["maker_fee_rate"]
        taker_fee_rate = values["taker_fee_rate"]
        slippage_rate = values["slippage_rate"]
        funding_rate = values["funding_rate"]
        funding_periods = values["funding_periods"]
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
        position_side: str = "long",
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
        if not isinstance(environment, EnvironmentMode) or not isinstance(market, MarketType):
            return {"ok": False, "issues": ["invalid_environment_or_market"], "position_size": 0.0}
        raw_values = (
            balance, risk_per_trade, entry, stop_loss, leverage, daily_loss_used,
            open_positions, current_exposure, max_leverage, max_risk_per_trade,
            max_daily_loss, max_open_positions, max_exposure,
        )
        if any(isinstance(value, bool) for value in raw_values if value is not None):
            return {"ok": False, "issues": ["boolean_numeric_input_not_allowed"], "position_size": 0.0}
        # Reject malformed/non-finite inputs before sizing. An explicit zero
        # risk cap must never fall back to a permissive default.
        try:
            numeric = {
                "balance": float(balance), "risk_per_trade": float(risk_per_trade),
                "entry": float(entry), "stop_loss": float(stop_loss),
                "leverage": float(leverage), "daily_loss_used": float(daily_loss_used),
                "open_positions": float(open_positions), "current_exposure": float(current_exposure),
            }
            optional_values = {
                "max_leverage": max_leverage, "max_risk_per_trade": max_risk_per_trade,
                "max_daily_loss": max_daily_loss, "max_open_positions": max_open_positions,
                "max_exposure": max_exposure,
            }
            numeric.update({key: float(value) for key, value in optional_values.items() if value is not None})
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "issues": ["invalid_numeric_input"], "position_size": 0.0}
        import math
        if not all(math.isfinite(value) for value in numeric.values()):
            return {"ok": False, "issues": ["non_finite_numeric_input"], "position_size": 0.0}
        balance, risk_per_trade = numeric["balance"], numeric["risk_per_trade"]
        entry, stop_loss, leverage = numeric["entry"], numeric["stop_loss"], numeric["leverage"]
        daily_loss_used, current_exposure = numeric["daily_loss_used"], numeric["current_exposure"]
        open_positions = numeric["open_positions"]
        if not open_positions.is_integer() or open_positions < 0:
            return {"ok": False, "issues": ["invalid_open_position_count"], "position_size": 0.0}
        open_positions = int(open_positions)
        if balance <= 0 or entry <= 0 or stop_loss <= 0:
            return {"ok": False, "issues": ["invalid_balance_or_prices"], "position_size": 0.0}
        if position_side not in {"long", "short"}:
            return {"ok": False, "issues": ["invalid_position_side"], "position_size": 0.0}
        if (position_side == "long" and stop_loss >= entry) or (position_side == "short" and stop_loss <= entry):
            return {"ok": False, "issues": ["stop_loss_wrong_side_for_position"], "position_size": 0.0}
        if risk_per_trade <= 0:
            return {"ok": False, "issues": ["risk_per_trade_must_be_positive"], "position_size": 0.0}
        configured_max_leverage = settings.max_leverage_cap if max_leverage is None else numeric["max_leverage"]
        configured_max_risk = settings.max_risk_per_trade_cap if max_risk_per_trade is None else numeric["max_risk_per_trade"]
        try:
            global_leverage_cap = float(settings.max_leverage_cap)
            global_risk_cap = float(settings.max_risk_per_trade_cap)
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "issues": ["invalid_global_risk_caps"], "position_size": 0.0}
        if not all(math.isfinite(value) for value in (configured_max_leverage, configured_max_risk, global_leverage_cap, global_risk_cap)):
            return {"ok": False, "issues": ["invalid_or_non_finite_risk_caps"], "position_size": 0.0}
        if min(configured_max_leverage, configured_max_risk, global_leverage_cap, global_risk_cap) <= 0:
            return {"ok": False, "issues": ["risk_caps_must_be_positive"], "position_size": 0.0}
        max_lev = min(configured_max_leverage, global_leverage_cap)
        max_risk = min(configured_max_risk, global_risk_cap)
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
        # Position size is quote-notional, capped independently by market.
        try:
            market_trade_cap = float(
                settings.max_spot_trade_notional if market == MarketType.SPOT
                else settings.max_futures_trade_notional if market == MarketType.FUTURES
                else 0.0
            )
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "issues": ["invalid_market_notional_cap"], "position_size": 0.0}
        if not math.isfinite(market_trade_cap) or (market != MarketType.EARN and market_trade_cap <= 0):
            return {"ok": False, "issues": ["invalid_market_notional_cap"], "position_size": 0.0}
        position_notional = min((risk_amount / stop_dist), balance * lev, market_trade_cap) if market != MarketType.EARN else 0.0
        if market_trade_cap > 0 and position_notional >= market_trade_cap:
            issues.append(f"per_trade_notional_cap_applied:{market_trade_cap:.2f}")
        qty = position_notional / entry if position_notional > 0 else 0.0
        # Portfolio/risk limits
        try:
            global_daily_cap = float(settings.max_daily_loss_cap)
            requested_daily_cap = float(
                max_daily_loss if max_daily_loss is not None else global_daily_cap
            )
            raw_max_pos = max_open_positions if max_open_positions is not None else settings.max_open_positions_cap
            if isinstance(raw_max_pos, bool):
                raise ValueError("boolean_max_open_positions")
            max_pos_float = float(raw_max_pos)
            max_exp = float(max_exposure) if max_exposure is not None else (balance * float(settings.max_portfolio_exposure_multiple))
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "issues": ["invalid_portfolio_limits"], "position_size": 0.0}
        if (not all(math.isfinite(value) for value in (global_daily_cap, requested_daily_cap, max_pos_float, max_exp))
                or max_pos_float < 0 or not max_pos_float.is_integer()):
            return {"ok": False, "issues": ["invalid_portfolio_limits"], "position_size": 0.0}
        if global_daily_cap <= 0 or global_daily_cap > 1 or requested_daily_cap < 0 or requested_daily_cap > 1:
            return {"ok": False, "issues": ["invalid_max_daily_loss"], "position_size": 0.0}
        # A caller may tighten the daily-loss limit, but can never relax the
        # project-wide ceiling with a stale or explicitly higher override.
        max_daily = min(requested_daily_cap, global_daily_cap)
        max_pos = int(max_pos_float)
        if daily_loss_used < 0 or max_pos < 0 or open_positions < 0 or current_exposure < 0 or max_exp < 0:
            return {"ok": False, "issues": ["invalid_portfolio_limits"], "position_size": 0.0}
        if balance > 0 and daily_loss_used >= balance * max_daily:
            issues.append("daily_loss_limit_reached")
        if open_positions >= max_pos:
            issues.append("max_open_positions_reached")
        if current_exposure + position_notional > max_exp:
            issues.append("max_exposure_exceeded")
        liq_note = None
        if market == MarketType.FUTURES and lev > 1:
            # Simplified educational estimate only; real liquidation depends on maintenance margin, mode, fees, etc.
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
            "open_positions": int(open_positions),
            "max_open_positions": max_pos,
            "current_exposure": round(float(current_exposure), 8),
            "max_exposure": round(max_exp, 8),
            "projected_exposure": round(float(current_exposure) + position_notional, 8),
            "liquidation_note": liq_note,
            "note": "Estimates for planning/testing; verify exchange precision, margin, liquidation and fees before real execution.",
        }
