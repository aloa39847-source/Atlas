"""Position Manager - SL/TP/Trailing Stop logic (simulation/testable)
Does NOT auto-execute real orders. Execution must go through approved safe paths.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from typing import Any


@dataclass
class ManagedPosition:
    symbol: str
    side: str  # long/buy or short/sell
    entry_price: float
    quantity: float
    stop_loss: float | None = None
    take_profit: float | None = None
    trailing_stop_pct: float | None = None  # e.g. 0.02 for 2%
    trailing_stop_price: float | None = None
    highest_price: float | None = None
    lowest_price: float | None = None

    def update_price(self, price: float) -> dict[str, Any]:
        action = None
        reason = None
        raw_values = (
            price, self.entry_price, self.quantity, self.trailing_stop_pct,
            self.stop_loss, self.take_profit, self.trailing_stop_price,
            self.highest_price, self.lowest_price,
        )
        if any(isinstance(value, bool) for value in raw_values if value is not None):
            return {"action": None, "reason": "boolean_position_value_not_allowed", "trailing_stop_price": self.trailing_stop_price, "price": price}
        try:
            price = float(price)
            entry = float(self.entry_price)
            quantity = float(self.quantity)
            trailing_pct = float(self.trailing_stop_pct) if self.trailing_stop_pct is not None else None
            optional = {
                "stop_loss": float(self.stop_loss) if self.stop_loss is not None else None,
                "take_profit": float(self.take_profit) if self.take_profit is not None else None,
                "trailing_stop_price": float(self.trailing_stop_price) if self.trailing_stop_price is not None else None,
                "highest_price": float(self.highest_price) if self.highest_price is not None else None,
                "lowest_price": float(self.lowest_price) if self.lowest_price is not None else None,
            }
        except (TypeError, ValueError, OverflowError):
            return {"action": None, "reason": "invalid_position_or_price", "trailing_stop_price": self.trailing_stop_price, "price": price}
        values = [price, entry, quantity]
        values.extend(v for v in optional.values() if v is not None)
        if trailing_pct is not None:
            values.append(trailing_pct)
        if (not all(math.isfinite(v) for v in values) or price <= 0 or entry <= 0 or quantity <= 0
                or any(v is not None and v <= 0 for v in optional.values())):
            return {"action": None, "reason": "invalid_position_or_price", "trailing_stop_price": self.trailing_stop_price, "price": price}
        if self.side not in ("long", "buy", "short", "sell"):
            return {"action": None, "reason": "invalid_position_side", "trailing_stop_price": self.trailing_stop_price, "price": price}
        if trailing_pct is not None and not 0 < trailing_pct <= 0.20:
            return {"action": None, "reason": "invalid_trailing_stop_pct", "trailing_stop_price": self.trailing_stop_price, "price": price}
        if self.side in ("long", "buy"):
            if optional["stop_loss"] is not None and optional["stop_loss"] >= entry:
                return {"action": None, "reason": "long_stop_loss_must_be_below_entry", "trailing_stop_price": self.trailing_stop_price, "price": price}
            if optional["take_profit"] is not None and optional["take_profit"] <= entry:
                return {"action": None, "reason": "long_take_profit_must_be_above_entry", "trailing_stop_price": self.trailing_stop_price, "price": price}
        else:
            if optional["stop_loss"] is not None and optional["stop_loss"] <= entry:
                return {"action": None, "reason": "short_stop_loss_must_be_above_entry", "trailing_stop_price": self.trailing_stop_price, "price": price}
            if optional["take_profit"] is not None and optional["take_profit"] >= entry:
                return {"action": None, "reason": "short_take_profit_must_be_below_entry", "trailing_stop_price": self.trailing_stop_price, "price": price}
        for name, value in optional.items():
            setattr(self, name, value)
        if self.side in ("long","buy"):
            if self.highest_price is None or price > self.highest_price:
                self.highest_price = price
            # SL
            if self.stop_loss is not None and price <= self.stop_loss:
                action = "close"; reason = "stop_loss"
            # TP
            elif self.take_profit is not None and price >= self.take_profit:
                action = "close"; reason = "take_profit"
            # Trailing
            elif trailing_pct and self.highest_price:
                trail = self.highest_price * (1 - trailing_pct)
                if self.trailing_stop_price is None or trail > self.trailing_stop_price:
                    self.trailing_stop_price = trail
                if price <= self.trailing_stop_price:
                    action = "close"; reason = "trailing_stop"
        else:  # short
            if self.lowest_price is None or price < self.lowest_price:
                self.lowest_price = price
            if self.stop_loss is not None and price >= self.stop_loss:
                action = "close"; reason = "stop_loss"
            elif self.take_profit is not None and price <= self.take_profit:
                action = "close"; reason = "take_profit"
            elif trailing_pct and self.lowest_price:
                trail = self.lowest_price * (1 + trailing_pct)
                if self.trailing_stop_price is None or trail < self.trailing_stop_price:
                    self.trailing_stop_price = trail
                if price >= self.trailing_stop_price:
                    action = "close"; reason = "trailing_stop"
        return {"action": action, "reason": reason, "trailing_stop_price": self.trailing_stop_price, "price": price}


class PositionRiskManager:
    def evaluate_positions(self, positions: list[ManagedPosition], prices: dict[str, float]) -> list[dict[str, Any]]:
        signals = []
        for p in positions:
            price = prices.get(p.symbol)
            if price is None:
                continue
            res = p.update_price(price)
            if res.get("action") == "close":
                signals.append({"symbol": p.symbol, "side": p.side, "quantity": p.quantity, "reason": res["reason"], "price": price})
        return signals
