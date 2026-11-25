"""Read-only triangular Spot arbitrage screening from public Binance top-of-book data.

This module estimates theoretical conversion paths only. It does not reserve
liquidity, account for exchange filters/minimum notionals, or submit orders.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from app.engines.market_data import MarketDataEngine


class ArbitrageAnalysisEngine:
    def __init__(self, market_data: MarketDataEngine | None = None) -> None:
        self.market_data = market_data or MarketDataEngine()

    @staticmethod
    def _asset(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("route_assets_must_be_strings")
        asset = value.strip().upper()
        if not asset or len(asset) > 20 or not asset.isalnum():
            raise ValueError("invalid_route_asset")
        return asset

    def analyze_triangle(
        self,
        route: list[str],
        quotes: dict[str, dict[str, Any]],
        *,
        starting_amount: float = 1000.0,
        fee_rate: float = 0.001,
        slippage_rate: float = 0.0005,
        min_profit_pct: float = 0.05,
        max_quote_age_seconds: float = 30.0,
        max_snapshot_skew_seconds: float = 15.0,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        try:
            if not isinstance(route, list) or len(route) != 4:
                raise ValueError("triangle_route_must_contain_four_assets")
            assets = [self._asset(item) for item in route]
            if assets[0] != assets[-1] or len(set(assets[:-1])) != 3:
                raise ValueError("route_must_be_a_closed_three_asset_cycle")
            if not isinstance(quotes, dict):
                raise ValueError("quotes_must_be_an_object")
            raw_config = (starting_amount, fee_rate, slippage_rate, min_profit_pct,
                          max_quote_age_seconds, max_snapshot_skew_seconds)
            if any(isinstance(value, bool) for value in raw_config):
                raise ValueError("boolean_arbitrage_configuration_not_allowed")
            values = [float(value) for value in raw_config]
            if not all(math.isfinite(value) for value in values):
                raise ValueError("non_finite_arbitrage_configuration")
            starting_amount, fee_rate, slippage_rate, min_profit_pct, max_age, max_skew = values
            if (starting_amount <= 0 or not 0 <= fee_rate < 1
                    or not 0 <= slippage_rate < 1 or min_profit_pct < 0
                    or max_age <= 0 or max_skew < 0):
                raise ValueError("arbitrage_configuration_out_of_range")
            current = now or datetime.now(timezone.utc)
            if current.tzinfo is None:
                raise ValueError("arbitrage_now_must_be_timezone_aware")

            normalized_quotes: dict[str, dict[str, Any]] = {}
            quote_times: list[datetime] = []
            for key, quote in quotes.items():
                if not isinstance(key, str) or not isinstance(quote, dict):
                    raise ValueError("invalid_quote_record")
                symbol = key.replace("/", "").upper()
                if (not symbol or not symbol.isalnum()
                        or quote.get("source") != "binance_public_read_only"):
                    raise ValueError("verified_binance_spot_public_quotes_required")
                if symbol in normalized_quotes:
                    raise ValueError("duplicate_normalized_market_quote")
                quote_symbol = str(quote.get("symbol") or key).replace("/", "").upper()
                if quote_symbol != symbol:
                    raise ValueError("quote_symbol_mismatch")
                if any(isinstance(quote.get(name), bool) for name in ("best_bid", "best_ask")):
                    raise ValueError("boolean_quote_price_not_allowed")
                bid, ask = float(quote["best_bid"]), float(quote["best_ask"])
                if not math.isfinite(bid) or not math.isfinite(ask) or bid <= 0 or ask <= bid:
                    raise ValueError("invalid_or_crossed_quote")
                stamp = datetime.fromisoformat(str(quote["fetched_at"]).replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError("quote_timestamp_must_be_timezone_aware")
                age = (current - stamp).total_seconds()
                if age < -5 or age > max_age:
                    raise ValueError("stale_or_future_quote")
                quote_times.append(stamp)
                normalized_quotes[symbol] = {"bid": bid, "ask": ask, "symbol": key, "fetched_at": stamp}
            if len(quote_times) != 3:
                raise ValueError("exactly_three_market_quotes_required")
            skew = (max(quote_times) - min(quote_times)).total_seconds()
            if skew > max_skew:
                raise ValueError("quote_snapshots_too_far_apart")

            amount = starting_amount
            legs = []
            for index in range(3):
                source_asset, target_asset = assets[index], assets[index + 1]
                direct = f"{source_asset}{target_asset}"
                inverse = f"{target_asset}{source_asset}"
                if direct in normalized_quotes:
                    quote = normalized_quotes[direct]
                    raw_output = amount * quote["bid"]
                    side = "sell_base_at_bid"
                elif inverse in normalized_quotes:
                    quote = normalized_quotes[inverse]
                    raw_output = amount / quote["ask"]
                    side = "buy_base_at_ask"
                else:
                    raise ValueError(f"missing_market_quote:{source_asset}/{target_asset}")
                after_slippage = raw_output * (1.0 - slippage_rate)
                output_amount = after_slippage * (1.0 - fee_rate)
                if not math.isfinite(output_amount) or output_amount <= 0:
                    raise ValueError("invalid_conversion_output")
                legs.append({
                    "from_asset": source_asset,
                    "to_asset": target_asset,
                    "market_symbol": quote["symbol"],
                    "execution_side": side,
                    "input_amount": round(amount, 12),
                    "output_before_costs": round(raw_output, 12),
                    "output_after_estimated_costs": round(output_amount, 12),
                })
                amount = output_amount

            profit = amount - starting_amount
            profit_pct = (profit / starting_amount) * 100.0
            return {
                "ok": True,
                "market": "spot",
                "route": assets,
                "starting_amount": starting_amount,
                "ending_amount_estimate": round(amount, 12),
                "profit_estimate": round(profit, 12),
                "profit_percent_estimate": round(profit_pct, 6),
                "candidate": profit_pct > min_profit_pct,
                "assessment": "estimated_edge_above_threshold" if profit_pct > min_profit_pct else "no_edge_above_threshold",
                "fees_per_leg": fee_rate,
                "slippage_per_leg": slippage_rate,
                "quote_snapshot_skew_seconds": round(skew, 3),
                "legs": legs,
                "source": "binance_public_read_only",
                "execution": "none",
                "limitations": [
                    "top-of-book estimate only; displayed depth is not reserved",
                    "exchange filters, minimum notionals, precision, latency and partial fills are not modeled",
                    "fees and slippage are configurable estimates, not account-specific Binance rates",
                    "not an executable arbitrage instruction or profitability guarantee",
                ],
            }
        except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
            return {"ok": False, "error": str(exc)[:200], "candidate": False, "execution": "none"}

    def fetch_and_analyze(
        self,
        route: list[str],
        *,
        starting_amount: float = 1000.0,
        fee_rate: float = 0.001,
        slippage_rate: float = 0.0005,
        min_profit_pct: float = 0.05,
    ) -> dict[str, Any]:
        try:
            if not isinstance(route, list) or len(route) != 4:
                return {"ok": False, "error": "triangle_route_must_contain_four_assets", "candidate": False}
            assets = [self._asset(item) for item in route]
            if assets[0] != assets[-1] or len(set(assets[:-1])) != 3:
                return {"ok": False, "error": "route_must_be_a_closed_three_asset_cycle", "candidate": False}
            quotes: dict[str, dict[str, Any]] = {}
            for index in range(3):
                left, right = assets[index], assets[index + 1]
                pair = f"{left}/{right}"
                inverse = f"{right}/{left}"
                # Binance symbols use BASE/QUOTE. Try the orientation that exists
                # in exchange metadata, without guessing or silently changing assets.
                try:
                    snapshot = self.market_data.get_public_order_book(pair, 20, "spot")
                except Exception:
                    snapshot = self.market_data.get_public_order_book(inverse, 20, "spot")
                quotes[snapshot["symbol"]] = snapshot
            return self.analyze_triangle(
                assets, quotes, starting_amount=starting_amount, fee_rate=fee_rate,
                slippage_rate=slippage_rate, min_profit_pct=min_profit_pct,
            )
        except Exception as exc:
            return {"ok": False, "error": "public_arbitrage_data_unavailable", "detail": str(exc)[:200], "candidate": False, "execution": "none"}
