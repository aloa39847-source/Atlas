"""Deterministic USD-M Futures maintenance-tier and cross-margin replay primitives.

This module is research-only: it does not call Binance authenticated endpoints or place orders.
Tier schedules must be supplied from an explicitly sourced exchangeInfo/leverage-bracket snapshot.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"invalid_{field}")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid_{field}") from exc
    if not math.isfinite(result):
        raise ValueError(f"invalid_{field}")
    return result


def normalize_maintenance_brackets(brackets: Sequence[Mapping[str, Any]]) -> list[dict[str, float]]:
    """Validate and normalize Binance bracket fields (notionalFloor/Cap, maintMarginRatio, cum)."""
    if not isinstance(brackets, Sequence) or isinstance(brackets, (str, bytes)) or not brackets:
        raise ValueError("maintenance_brackets_required")
    normalized = []
    for index, item in enumerate(brackets):
        if not isinstance(item, Mapping):
            raise ValueError(f"invalid_bracket_{index}")
        floor = _finite(item.get("notionalFloor"), "notional_floor")
        cap = _finite(item.get("notionalCap"), "notional_cap")
        rate = _finite(item.get("maintMarginRatio"), "maintenance_rate")
        cum = _finite(item.get("cum", 0.0), "maintenance_deduction")
        if floor < 0 or cap <= floor or not 0 < rate < 1 or cum < 0:
            raise ValueError(f"invalid_bracket_{index}")
        normalized.append({"notional_floor": floor, "notional_cap": cap,
                           "maintenance_rate": rate, "cum": cum})
    normalized.sort(key=lambda row: row["notional_floor"])
    if normalized[0]["notional_floor"] != 0:
        raise ValueError("maintenance_brackets_must_start_at_zero")
    for index, row in enumerate(normalized):
        if index and (row["notional_floor"] != normalized[index - 1]["notional_cap"]):
            raise ValueError("maintenance_bracket_gap_or_overlap")
    return normalized


def maintenance_requirement(notional: float, brackets: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Return Binance-style bracket maintenance: notional * MMR - cumulative deduction."""
    value = _finite(notional, "notional")
    if value < 0:
        raise ValueError("invalid_notional")
    tiers = normalize_maintenance_brackets(brackets)
    tier = next((row for row in tiers if row["notional_floor"] <= value < row["notional_cap"]), None)
    if tier is None and math.isclose(value, tiers[-1]["notional_cap"]):
        tier = tiers[-1]
    if tier is None:
        raise ValueError("notional_outside_supplied_maintenance_brackets")
    amount = max(0.0, value * tier["maintenance_rate"] - tier["cum"])
    return {"maintenance_margin": amount, "maintenance_rate": tier["maintenance_rate"],
            "cum": tier["cum"], "notional": value,
            "tier_floor": tier["notional_floor"], "tier_cap": tier["notional_cap"]}


def simulate_cross_margin_account(
    *, wallet_balance: float, positions: Sequence[Mapping[str, Any]],
    liquidation_fee_rate: float = 0.005,
) -> dict[str, Any]:
    """Mark multiple positions against one shared cross wallet and aggregate maintenance.

    Each position must carry its own verified bracket snapshot. Funding/fees already
    realized must be reflected in wallet_balance; unrealized PnL is marked here.
    This is a conservative account-health/breach model, not Binance's exact ADL,
    liquidation queue, insurance-fund, or exchange liquidation-price engine.
    """
    wallet = _finite(wallet_balance, "wallet_balance")
    fee_rate = _finite(liquidation_fee_rate, "liquidation_fee_rate")
    if wallet <= 0 or not 0 <= fee_rate < 0.1:
        return {"ok": False, "error": "cross_margin_parameters_out_of_range", "no_live_orders": True}
    if not isinstance(positions, Sequence) or isinstance(positions, (str, bytes)) or not positions:
        return {"ok": False, "error": "cross_margin_positions_required", "no_live_orders": True}
    marked = []
    total_upnl = total_maintenance = total_liquidation_fee = 0.0
    try:
        for index, position in enumerate(positions):
            if not isinstance(position, Mapping):
                raise ValueError(f"invalid_position_{index}")
            symbol = str(position.get("symbol", "")).strip().upper()
            side = str(position.get("side", "")).strip().lower()
            qty = _finite(position.get("quantity"), "quantity")
            entry = _finite(position.get("entry_price"), "entry_price")
            mark = _finite(position.get("mark_price"), "mark_price")
            leverage = _finite(position.get("leverage"), "leverage")
            if not symbol or side not in {"long", "short"} or qty <= 0 or entry <= 0 or mark <= 0 or leverage < 1:
                raise ValueError(f"invalid_position_{index}")
            brackets = position.get("maintenance_brackets")
            if brackets is None:
                raise ValueError(f"maintenance_brackets_missing_{symbol}")
            notional = qty * mark
            sign = 1.0 if side == "long" else -1.0
            upnl = sign * qty * (mark - entry)
            requirement = maintenance_requirement(notional, brackets)
            estimated_liquidation_fee = notional * fee_rate
            total_upnl += upnl
            total_maintenance += requirement["maintenance_margin"]
            total_liquidation_fee += estimated_liquidation_fee
            marked.append({
                "symbol": symbol, "side": side, "quantity": qty, "entry_price": entry,
                "mark_price": mark, "notional": round(notional, 8),
                "unrealized_pnl": round(upnl, 8), **requirement,
                "estimated_liquidation_fee": round(estimated_liquidation_fee, 8),
            })
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "no_live_orders": True}

    equity = wallet + total_upnl
    required = total_maintenance + total_liquidation_fee
    margin_surplus = equity - required
    ratio = total_maintenance / equity if equity > 0 else None
    return {
        "ok": True, "margin_mode": "cross", "wallet_balance": round(wallet, 8),
        "unrealized_pnl": round(total_upnl, 8), "cross_equity": round(equity, 8),
        "maintenance_margin": round(total_maintenance, 8),
        "estimated_liquidation_fees": round(total_liquidation_fee, 8),
        "required_margin_plus_fees": round(required, 8),
        "margin_surplus": round(margin_surplus, 8),
        "maintenance_to_equity_ratio": round(ratio, 8) if ratio is not None else None,
        "liquidation_breach": equity <= required,
        "positions": marked, "tier_source": "caller_supplied_verified_binance_bracket_snapshot",
        "model": "shared_wallet_aggregate_maintenance_approximation",
        "limitations": ["No exact Binance liquidation queue or ADL simulation",
                        "Funding and realized fees must be included in wallet_balance",
                        "No exchange execution or authenticated account access"],
        "execution": "simulation_only", "no_live_orders": True,
    }
