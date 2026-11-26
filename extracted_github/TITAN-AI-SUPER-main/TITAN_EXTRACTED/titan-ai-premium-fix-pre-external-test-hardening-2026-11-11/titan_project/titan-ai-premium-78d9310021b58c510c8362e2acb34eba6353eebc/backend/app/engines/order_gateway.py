from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Any

from app.core.enums import MarketType
from app.core.config import settings
from .compliance import compliance_engine


@dataclass
class GatewayDecision:
    ok: bool
    reason: str
    normalized: dict[str, Any]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reason": self.reason, "normalized": self.normalized, "warnings": self.warnings}


class PreTradeOrderGateway:
    """Independent pre-trade safety layer. No AI model may bypass it.

    This validates request shape and basic safety constraints. Exchange-specific
    filters (tick/step/minNotional), balances, and live order state require
    Binance adapter integration and reconciliation.
    """

    def validate(
        self,
        *,
        symbol: str,
        market: MarketType,
        side: str,
        order_type: str,
        quantity: float | None,
        price: float | None = None,
        reference_price: float | None = None,
        max_notional: float | None = None,
        idempotency_key: str | None = None,
        reduce_only: bool = False,
        verified_position_close: bool = False,
        leverage: float = 1.0,
        environment_mode: str = "demo",
        live_enabled: bool = False,
        api_permissions: list[str] | None = None,
        bypass_attempt: bool = False,
        geo_verified: bool | None = None,
        kyc_verified: bool | None = None,
        rate_limit_ok: bool | None = None,
        exchange_filters: dict | None = None,
        market_conduct_flags: dict[str, Any] | None = None,
        current_position: float | None = None,
        current_open_orders_count: int | None = None,
        current_exchange_open_orders_count: int | None = None,
    ) -> GatewayDecision:
        warnings: list[str] = []
        if not isinstance(market, MarketType):
            return GatewayDecision(False, "invalid_market_type", {}, warnings)
        if not isinstance(symbol, str) or not isinstance(side, str) or not isinstance(order_type, str):
            return GatewayDecision(False, "invalid_order_text_fields", {}, warnings)
        if any(isinstance(value, bool) for value in (quantity, price, reference_price, max_notional, leverage)):
            return GatewayDecision(False, "boolean_numeric_input_not_allowed", {}, warnings)
        if type(reduce_only) is not bool:
            return GatewayDecision(False, "reduce_only_must_be_boolean", {}, warnings)
        if type(verified_position_close) is not bool:
            return GatewayDecision(False, "verified_position_close_must_be_boolean", {}, warnings)
        if idempotency_key is not None and not isinstance(idempotency_key, str):
            return GatewayDecision(False, "invalid_idempotency_key", {}, warnings)
        if type(live_enabled) is not bool:
            return GatewayDecision(False, "invalid_live_enabled_flag", {}, warnings)
        if api_permissions is not None and not isinstance(api_permissions, list):
            return GatewayDecision(False, "invalid_api_permissions", {}, warnings)
        if exchange_filters is not None and not isinstance(exchange_filters, dict):
            return GatewayDecision(False, "invalid_exchange_filters", {}, warnings)
        # Fail closed on malformed, NaN, or infinite numeric order inputs
        # before compliance checks or arithmetic can raise or accept them.
        try:
            quantity = float(quantity) if quantity is not None else None
            price = float(price) if price is not None else None
            reference_price = float(reference_price) if reference_price is not None else None
            max_notional = float(max_notional) if max_notional is not None else None
            leverage = float(leverage)
        except (TypeError, ValueError, OverflowError):
            return GatewayDecision(False, "invalid_numeric_input", {}, warnings)
        numeric_values = [v for v in (quantity, price, reference_price, max_notional, leverage) if v is not None]
        if not all(math.isfinite(v) for v in numeric_values):
            return GatewayDecision(False, "non_finite_numeric_input", {}, warnings)
        if quantity is None or quantity <= 0:
            return GatewayDecision(False, "invalid_quantity", {"quantity": quantity}, warnings)
        if price is not None and price <= 0:
            return GatewayDecision(False, "invalid_price", {"price": price}, warnings)
        if reference_price is not None and reference_price <= 0:
            return GatewayDecision(False, "invalid_reference_price", {"reference_price": reference_price}, warnings)
        if max_notional is not None and max_notional <= 0:
            return GatewayDecision(False, "invalid_max_notional", {"max_notional": max_notional}, warnings)
        comp = compliance_engine.check_order_action(
            market=market, symbol=symbol or "", side=side or "", quantity=quantity, price=price,
            environment_mode=environment_mode, live_enabled=live_enabled, api_permissions=api_permissions,
            bypass_attempt=bypass_attempt, geo_verified=geo_verified, kyc_verified=kyc_verified, rate_limit_ok=rate_limit_ok, market_conduct_flags=market_conduct_flags,
        )
        if not comp.ok or comp.action == "block":
            return GatewayDecision(False, f"compliance_block:{comp.reason}", {"compliance": comp.to_dict()}, warnings)
        if comp.action == "review_required":
            return GatewayDecision(False, f"compliance_review_required:{comp.reason}", {"compliance": comp.to_dict()}, warnings)
        if live_enabled and (not idempotency_key or len(idempotency_key.strip()) < 8):
            return GatewayDecision(False, "live_order_idempotency_key_required", {}, warnings)
        warnings.append(f"compliance:{comp.action}:{comp.reason}")
        sym = (symbol or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,20}(?:/[A-Z0-9]{2,20})?", sym):
            return GatewayDecision(False, "invalid_symbol", {"symbol": symbol}, warnings)
        sd = (side or "").strip().lower()
        if market == MarketType.SPOT and sd not in {"buy", "sell"}:
            return GatewayDecision(False, "invalid_spot_side", {"side": side}, warnings)
        if market == MarketType.FUTURES and sd not in {"buy", "sell", "long", "short"}:
            return GatewayDecision(False, "invalid_futures_side", {"side": side}, warnings)
        if market == MarketType.EARN:
            return GatewayDecision(False, "earn_not_order_execution", {"market": market.value}, warnings)
        if reduce_only and market != MarketType.FUTURES:
            return GatewayDecision(False, "reduce_only_futures_only", {"market": market.value}, warnings)
        if reduce_only and sd not in {"buy", "sell"}:
            return GatewayDecision(False, "reduce_only_requires_buy_or_sell_side", {"side": sd}, warnings)
        if verified_position_close and (market != MarketType.SPOT or sd != "sell"):
            return GatewayDecision(False, "verified_position_close_spot_sell_only", {"market": market.value, "side": sd}, warnings)
        if leverage < 1.0:
            return GatewayDecision(False, "invalid_leverage", {"leverage": leverage}, warnings)
        if market == MarketType.SPOT and leverage != 1.0:
            return GatewayDecision(False, "spot_leverage_not_supported", {"leverage": leverage}, warnings)
        if market == MarketType.FUTURES and leverage > settings.max_leverage_cap:
            return GatewayDecision(False, "leverage_exceeds_limit", {"leverage": leverage, "max_leverage": settings.max_leverage_cap}, warnings)
        ot = (order_type or "").strip().lower()
        if ot not in {"market", "limit", "stop", "stop_limit"}:
            return GatewayDecision(False, "unsupported_order_type", {"order_type": ot}, warnings)
        px = price if price is not None else reference_price
        if px is None:
            return GatewayDecision(False, "reference_price_required_for_pretrade", {}, warnings)
        if reference_price and price and reference_price > 0:
            deviation = abs(price - reference_price) / reference_price
            if deviation > 0.2:
                return GatewayDecision(False, "price_deviation_too_large", {"deviation": deviation}, warnings)
            if deviation > 0.03:
                warnings.append("price_deviates_from_reference")
        notional = (px or 0.0) * quantity
        configured_cap = (
            settings.max_spot_trade_notional if market == MarketType.SPOT
            else min(settings.max_futures_trade_notional, settings.max_futures_trade_margin * leverage)
            if market == MarketType.FUTURES else 0.0
        )
        effective_cap = configured_cap if max_notional is None else min(configured_cap, float(max_notional))
        # A validated Futures reduce-only close lowers an existing position;
        # it must remain possible even when mark-price appreciation pushes its
        # exit notional above the entry cap. An explicit caller-supplied cap
        # remains enforceable.
        reduce_only_exit = market == MarketType.FUTURES and reduce_only and max_notional is None
        position_close_exit = market == MarketType.SPOT and verified_position_close and max_notional is None
        if idempotency_key is not None and len(idempotency_key.strip()) < 8:
            return GatewayDecision(False, "weak_idempotency_key", {"idempotency_key": "masked"}, warnings)
        # Binance exchange filters are authoritative exchange constraints.
        # Use Decimal(str(...)) so tick/step checks do not depend on float modulo.
        filters_checked = False
        if exchange_filters:
            filters_checked = True
            numeric_filter_keys = (
                "tickSize", "stepSize", "lotStepSize", "marketStepSize",
                "minQty", "maxQty", "marketMinQty", "marketMaxQty",
                "minPrice", "maxPrice", "minNotional", "maxNotional",
                "percentMultiplierUp", "percentMultiplierDown",
                "bidMultiplierUp", "bidMultiplierDown",
                "askMultiplierUp", "askMultiplierDown",
                "avgPriceMins", "icebergPartsLimit", "maxNumOrders", "exchangeMaxNumOrders",
                "maxNumAlgoOrders", "maxPosition",
            )
            parsed: dict[str, Decimal] = {}
            try:
                for key in numeric_filter_keys:
                    raw = exchange_filters.get(key)
                    if raw is None:
                        continue
                    if isinstance(raw, bool):
                        raise ValueError("boolean_filter_value")
                    value = Decimal(str(raw))
                    if not value.is_finite() or value < 0:
                        raise ValueError("invalid_filter_value")
                    parsed[key] = value
                px_dec = Decimal(str(price if price is not None else reference_price))
                qty_dec = Decimal(str(quantity))
                notional_dec = px_dec * qty_dec
                market_order = ot == "market"
                if market == MarketType.SPOT and sd == "buy" and "maxPosition" in parsed:
                    if current_position is None:
                        return GatewayDecision(False, "position_filter_state_unavailable", {}, warnings)
                    position_dec = Decimal(str(current_position))
                    if not position_dec.is_finite() or position_dec < 0:
                        return GatewayDecision(False, "invalid_position_filter_state", {}, warnings)
                    if position_dec + qty_dec > parsed["maxPosition"]:
                        return GatewayDecision(False, "max_position_exceeded", {
                            "currentPosition": str(position_dec), "orderQuantity": str(qty_dec),
                            "maxPosition": str(parsed["maxPosition"]),
                        }, warnings)
                if ot == "limit":
                    symbol_order_limit = parsed.get("maxNumOrders", Decimal("0"))
                    exchange_order_limit = parsed.get("exchangeMaxNumOrders", Decimal("0"))
                    if symbol_order_limit > 0:
                        if current_open_orders_count is None:
                            return GatewayDecision(False, "open_order_count_unavailable", {}, warnings)
                        if current_open_orders_count >= symbol_order_limit:
                            return GatewayDecision(False, "max_num_orders_exceeded", {
                                "openOrders": current_open_orders_count, "maxNumOrders": str(symbol_order_limit),
                            }, warnings)
                    if exchange_order_limit > 0:
                        if current_exchange_open_orders_count is None:
                            return GatewayDecision(False, "exchange_open_order_count_unavailable", {}, warnings)
                        if current_exchange_open_orders_count >= exchange_order_limit:
                            return GatewayDecision(False, "exchange_max_num_orders_exceeded", {
                                "openOrders": current_exchange_open_orders_count,
                                "exchangeMaxNumOrders": str(exchange_order_limit),
                            }, warnings)

                min_qty_key = "marketMinQty" if market_order and "marketMinQty" in parsed else "minQty"
                max_qty_key = "marketMaxQty" if market_order and "marketMaxQty" in parsed else "maxQty"
                step_key = "marketStepSize" if market_order and parsed.get("marketStepSize", Decimal("0")) > 0 else (
                    "lotStepSize" if not market_order and parsed.get("lotStepSize", Decimal("0")) > 0 else "stepSize"
                )
                min_qty = parsed.get(min_qty_key, Decimal("0"))
                max_qty = parsed.get(max_qty_key, Decimal("0"))
                step = parsed.get(step_key, Decimal("0"))
                if min_qty > 0 and qty_dec < min_qty:
                    return GatewayDecision(False, "quantity_below_min_qty", {"quantity": str(qty_dec), "minQty": str(min_qty)}, warnings)
                if max_qty > 0 and qty_dec > max_qty:
                    return GatewayDecision(False, "quantity_above_max_qty", {"quantity": str(qty_dec), "maxQty": str(max_qty)}, warnings)
                if step > 0 and (qty_dec - min_qty) % step != 0:
                    return GatewayDecision(False, "quantity_not_aligned_to_step_size", {"quantity": str(qty_dec), "stepSize": str(step)}, warnings)

                tick = parsed.get("tickSize", Decimal("0"))
                min_price = parsed.get("minPrice", Decimal("0"))
                max_price = parsed.get("maxPrice", Decimal("0"))
                if price is not None:
                    if min_price > 0 and px_dec < min_price:
                        return GatewayDecision(False, "price_below_min_price", {"price": str(px_dec), "minPrice": str(min_price)}, warnings)
                    if max_price > 0 and px_dec > max_price:
                        return GatewayDecision(False, "price_above_max_price", {"price": str(px_dec), "maxPrice": str(max_price)}, warnings)
                    if tick > 0 and (px_dec - min_price) % tick != 0:
                        return GatewayDecision(False, "price_not_aligned_to_tick_size", {"price": str(px_dec), "tickSize": str(tick)}, warnings)

                min_notional = parsed.get("minNotional", Decimal("0"))
                max_notional = parsed.get("maxNotional", Decimal("0"))
                apply_min_market = exchange_filters.get("applyMinToMarket", True)
                apply_max_market = exchange_filters.get("applyMaxToMarket", True)
                if type(apply_min_market) is not bool and apply_min_market not in (0, 1, 0.0, 1.0):
                    raise ValueError("invalid_apply_min_to_market")
                if type(apply_max_market) is not bool and apply_max_market not in (0, 1, 0.0, 1.0):
                    raise ValueError("invalid_apply_max_to_market")
                if min_notional > 0 and (not market_order or bool(apply_min_market)) and notional_dec < min_notional:
                    return GatewayDecision(False, "below_min_notional", {"notional": str(notional_dec), "minNotional": str(min_notional)}, warnings)
                if max_notional > 0 and (not market_order or bool(apply_max_market)) and notional_dec > max_notional:
                    return GatewayDecision(False, "above_max_notional", {"notional": str(notional_dec), "maxNotional": str(max_notional)}, warnings)

                # Percent-price filters are evaluated against the exchange reference
                # price supplied by the adapter; fail closed if a required bound exists.
                ref_dec = Decimal(str(reference_price)) if reference_price is not None else None
                if price is not None and ref_dec is not None:
                    if "percentMultiplierUp" in parsed and "percentMultiplierDown" in parsed:
                        low = ref_dec * parsed["percentMultiplierDown"]
                        high = ref_dec * parsed["percentMultiplierUp"]
                        if px_dec < low or px_dec > high:
                            return GatewayDecision(False, "price_outside_percent_price_filter", {"price": str(px_dec), "min": str(low), "max": str(high)}, warnings)
                    up_key, down_key = (("bidMultiplierUp", "bidMultiplierDown") if sd in {"buy", "long"} else ("askMultiplierUp", "askMultiplierDown"))
                    if up_key in parsed and down_key in parsed:
                        low = ref_dec * parsed[down_key]
                        high = ref_dec * parsed[up_key]
                        if px_dec < low or px_dec > high:
                            return GatewayDecision(False, "price_outside_percent_price_by_side_filter", {"price": str(px_dec), "min": str(low), "max": str(high)}, warnings)
            except (InvalidOperation, ValueError, TypeError, OverflowError) as exc:
                return GatewayDecision(False, "invalid_exchange_filters", {"error": str(exc)}, warnings)
        else:
            warnings.append("exchange_filters_balance_reconciliation_required_before_live_send")
        if px and notional > effective_cap and not (reduce_only_exit or position_close_exit):
            return GatewayDecision(False, "notional_exceeds_limit", {"notional": notional, "max_notional": effective_cap}, warnings)
        normalized = {
            "symbol": sym, "market": market.value, "side": sd, "order_type": ot,
            "quantity": quantity, "price": price, "reference_price": reference_price,
            "notional_estimate": notional if px else None, "max_notional": effective_cap, "idempotency_key_present": bool(idempotency_key),
            "reduce_only": bool(reduce_only), "leverage": leverage,
            "exchange_filters_checked": filters_checked,
            "notional_cap_bypassed_for_reduce_only": reduce_only_exit,
            "notional_cap_bypassed_for_verified_spot_close": position_close_exit,
            "exchange_balance_checked": False,
        }
        if not filters_checked:
            warnings.append("exchange_filters_balance_reconciliation_required_before_live_send")
        return GatewayDecision(True, "accepted_for_precheck", normalized, warnings)
