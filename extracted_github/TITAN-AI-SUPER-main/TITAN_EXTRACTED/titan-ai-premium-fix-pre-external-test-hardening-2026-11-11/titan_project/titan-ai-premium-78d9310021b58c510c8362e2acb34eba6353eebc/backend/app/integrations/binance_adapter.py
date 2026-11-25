"""Environment-isolated Binance execution adapter for Spot and USD-M Futures.
Live execution is fail-closed: server setting + explicit persisted user confirmation
+ environment-specific credentials + read-only permission checks are required.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import time
import threading
from email.utils import parsedate_to_datetime
import urllib.parse
import urllib.request
import urllib.error
import uuid
from dataclasses import dataclass
from typing import Any

from app.core.enums import MarketType
from app.engines.compliance import compliance_engine
from app.engines.order_gateway import PreTradeOrderGateway
from app.core.config import settings


SPOT_TESTNET_BASE = "https://testnet.binance.vision"
FUTURES_TESTNET_BASE = "https://testnet.binancefuture.com"
SPOT_LIVE_BASE = "https://api.binance.com"
FUTURES_LIVE_BASE = "https://fapi.binance.com"
TESTNET_BASES = {SPOT_TESTNET_BASE, FUTURES_TESTNET_BASE}
LIVE_BASES = {SPOT_LIVE_BASE, FUTURES_LIVE_BASE}
ALLOWED_BASES = TESTNET_BASES | LIVE_BASES

# Binance rate limits are IP-scoped. Share a cooldown across all adapter
# instances in this process and never retry automatically after 418/429.
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_UNTIL = 0.0


def _rate_limit_remaining() -> float:
    with _RATE_LIMIT_LOCK:
        return max(0.0, _RATE_LIMIT_UNTIL - time.time())


def _record_rate_limit(retry_after: str | None) -> float:
    global _RATE_LIMIT_UNTIL
    delay = 60.0  # conservative fallback when Binance omits/malforms Retry-After
    if retry_after:
        try:
            delay = max(1.0, float(retry_after))
        except (TypeError, ValueError, OverflowError):
            try:
                parsed = parsedate_to_datetime(retry_after)
                delay = max(1.0, parsed.timestamp() - time.time())
            except Exception:
                delay = 60.0
    delay = min(delay, 259200.0)  # Binance documents bans up to three days
    with _RATE_LIMIT_LOCK:
        _RATE_LIMIT_UNTIL = max(_RATE_LIMIT_UNTIL, time.time() + delay)
        return max(0.0, _RATE_LIMIT_UNTIL - time.time())


def _resolve_secret(ref: str | None) -> str | None:
    if not ref:
        return None
    if ref.startswith("env:"):
        return os.getenv(ref[4:], "") or None
    if ref.startswith("stored:"):
        try:
            from app.services.credentials import load_binance_credentials
            parts = ref.split(":")
            if len(parts) == 4 and parts[1] in {"testnet", "live"} and parts[2] in {"spot", "futures"}:
                environment, market, credential = parts[1], parts[2], parts[3]
                c = load_binance_credentials(environment, market)
                if credential == "api_key":
                    return c.get("api_key") or None
                if credential == "api_secret":
                    return c.get("api_secret") or None
                return None
            environment = "live" if ref.startswith("stored:live_") else "testnet"
            c = load_binance_credentials(environment)
            if ref in {"stored:api_key", "stored:live_api_key"}:
                return c.get("api_key") or None
            if ref in {"stored:api_secret", "stored:live_api_secret"}:
                return c.get("api_secret") or None
        except Exception:
            return None
    return ref


def _sign(params: dict, secret: str) -> str:
    query = urllib.parse.urlencode(params)
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


@dataclass
class BinanceConfig:
    enabled: bool = False
    environment: str = "testnet"  # testnet only for external; demo for internal shadow
    market: str = "spot"  # spot or futures
    api_key_ref: str | None = None  # env:BINANCE_TESTNET_API_KEY
    api_secret_ref: str | None = None  # environment-specific secret reference
    live_confirmed_by_user: bool = False  # must be set from persisted explicit user action
    live_geo_eligible_confirmed: bool | None = None
    live_kyc_confirmed: bool | None = None
    live_rate_limit_control_enabled: bool = True
    live_worker_ready: bool = False


class BinanceTestnetAdapter:
    name = "binance_testnet"

    def __init__(self, config: BinanceConfig | None = None):
        self.config = config or BinanceConfig()
        self.gateway = PreTradeOrderGateway()

    def _base_url(self) -> str:
        if self.config.environment == "live":
            return FUTURES_LIVE_BASE if self.config.market == "futures" else SPOT_LIVE_BASE
        if self.config.environment != "testnet":
            raise RuntimeError("unsupported_binance_environment")
        return FUTURES_TESTNET_BASE if self.config.market == "futures" else SPOT_TESTNET_BASE

    def _api_key(self) -> str | None:
        return _resolve_secret(self.config.api_key_ref)

    def _api_secret(self) -> str | None:
        return _resolve_secret(self.config.api_secret_ref)

    def _request(self, method: str, path: str, signed: bool = False, params: dict | None = None) -> dict:
        base = self._base_url()
        allowed = LIVE_BASES if self.config.environment == "live" else TESTNET_BASES
        if base not in allowed:
            raise RuntimeError("binance_environment_endpoint_mismatch")
        remaining = _rate_limit_remaining()
        if remaining > 0:
            return {"error": "rate_limit_cooldown", "status": 429,
                    "retry_after_seconds": round(remaining, 3),
                    "note": "No request sent; waiting for Binance Retry-After cooldown."}
        url = base + path
        params = dict(params or {})
        headers = {"User-Agent": "TITAN-AI-PREMIUM/1.0"}
        key = self._api_key()
        secret = self._api_secret()
        if key:
            headers["X-MBX-APIKEY"] = key
        if signed:
            if not secret:
                raise RuntimeError("missing_api_secret_for_signed_request")
            params["timestamp"] = int(time.time() * 1000)
            params.setdefault("recvWindow", 5000)
            params["signature"] = _sign(params, secret)
        if method.upper() == "GET":
            if params:
                url += "?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers=headers, method="GET")
        else:
            data = urllib.parse.urlencode(params).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode()
                try:
                    return json.loads(body)
                except Exception:
                    return {"raw": body}
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode()
            except Exception:
                err_body = ""
            status = getattr(e, "code", None)
            if status in {418, 429}:
                retry_after = e.headers.get("Retry-After") if getattr(e, "headers", None) else None
                delay = _record_rate_limit(retry_after)
                return {"error": "binance_rate_limit_exceeded", "status": status,
                        "retry_after_seconds": round(delay, 3), "body": err_body[:1000],
                        "note": "Requests paused; automatic retry is disabled to avoid IP ban escalation."}
            return {"error": "http_error", "status": status, "body": err_body[:1000]}
        except Exception as e:
            return {"error": str(e)}

    def get_futures_maintenance_brackets(self, symbol: str) -> dict[str, Any]:
        """Read the account's USD-M maintenance brackets from Futures Testnet only.

        Binance's leverageBracket endpoint is a signed USER_DATA GET. This method
        never uses a Live base URL and never submits or changes an exchange order.
        """
        if self.config.environment != "testnet" or self.config.market != "futures":
            raise RuntimeError("maintenance_brackets_testnet_futures_only")
        if not self._api_key() or not self._api_secret():
            raise RuntimeError("futures_testnet_credentials_required_for_maintenance_brackets")
        from app.data.binance_futures_public import BinanceFuturesPublicData
        from app.engines.cross_margin import normalize_maintenance_brackets
        normalized_symbol = BinanceFuturesPublicData._symbol(symbol)
        result = self._request(
            "GET", "/fapi/v1/leverageBracket", signed=True,
            params={"symbol": normalized_symbol},
        )
        if isinstance(result, dict) and result.get("symbol"):
            row = result if str(result.get("symbol", "")).upper() == normalized_symbol else None
        elif isinstance(result, list) and result:
            row = next((item for item in result if isinstance(item, dict)
                        and str(item.get("symbol", "")).upper() == normalized_symbol), None)
        else:
            code = result.get("status") if isinstance(result, dict) else None
            raise RuntimeError(f"binance_testnet_leverage_bracket_unavailable:{code or 'invalid_response'}")
        if row is None or not isinstance(row.get("brackets"), list):
            raise RuntimeError("binance_testnet_leverage_bracket_symbol_missing")
        raw_coef = row.get("notionalCoef", 1.0)
        if isinstance(raw_coef, bool):
            raise RuntimeError("binance_testnet_invalid_notional_coefficient")
        try:
            notional_coef = float(raw_coef)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("binance_testnet_invalid_notional_coefficient") from exc
        if not math.isfinite(notional_coef) or notional_coef <= 0:
            raise RuntimeError("binance_testnet_invalid_notional_coefficient")
        # Binance documents this as a user-specific bracket multiplier but does not
        # define its exact application to every field here. Never guess its scaling.
        if not math.isclose(notional_coef, 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise RuntimeError("binance_testnet_adjusted_notional_coefficient_not_supported")
        tiers = normalize_maintenance_brackets(row["brackets"])
        normalized = [
            {"notionalFloor": tier["notional_floor"], "notionalCap": tier["notional_cap"],
             "maintMarginRatio": tier["maintenance_rate"], "cum": tier["cum"]}
            for tier in tiers
        ]
        return {
            "ok": True, "symbol": normalized_symbol, "maintenance_brackets": normalized,
            "source": "binance_futures_testnet_signed_read_only",
            "retrieved_at": int(time.time() * 1000), "notional_coef": notional_coef, "environment": "testnet",
            "market": "futures", "no_orders_submitted": True, "live_trading": False,
        }

    def status(self) -> dict[str, Any]:
        environment = self.config.environment
        if environment not in {"testnet", "live"}:
            return {"provider": self.name, "configured": False, "connected": False, "error": "unsupported_binance_environment"}
        configured = bool(self._api_key() and self._api_secret())
        result = {
            "provider": "binance_" + environment,
            "environment": environment,
            "market": self.config.market,
            "configured": configured,
            "base_url": self._base_url(),
            "withdrawal_permissions": False,
            "live_blocked": environment == "live" and not (
                self.config.live_confirmed_by_user and self.config.live_worker_ready
            ),
        }
        if configured:
            try:
                if self.config.market == "futures":
                    acct = self._request("GET", "/fapi/v2/account", signed=True)
                else:
                    acct = self._request("GET", "/api/v3/account", signed=True)
                result["connected"] = isinstance(acct, dict) and "error" not in acct
                result["account_check"] = "ok" if result["connected"] else "error"
                if environment == "testnet" and result["connected"]:
                    result["trade_permission"] = acct.get("canTrade") is True
                    result["execution_ready"] = result["trade_permission"]
                    if not result["trade_permission"]:
                        result["permission_check"] = "binance_api_key_trade_permission_missing"
                if environment == "live" and result["connected"]:
                    result["trade_permission"] = acct.get("canTrade") is True
                    permissions = self._request("GET", "/sapi/v1/account/apiRestrictions", signed=True)
                    if not isinstance(permissions, dict) or permissions.get("error"):
                        result["permissions_verified"] = False
                        result["execution_ready"] = False
                        result["permission_check"] = "unavailable"
                    else:
                        restriction_error = self._validate_live_api_restrictions(permissions)
                        result["ip_allowlist_enabled"] = permissions.get("ipRestrict") is True
                        result["ip_allowlist_entries_verified"] = (
                            isinstance(permissions.get("ipList"), list) and bool(permissions.get("ipList"))
                        )
                        result["ip_allowlist_note"] = (
                            "Binance permission endpoint reports ipRestrict but may not return the actual IP entries; "
                            "verify the server egress IP in Binance API Management."
                        )
                        result["withdrawal_permissions"] = bool(permissions.get("enableWithdrawals"))
                        result["transfer_permissions"] = bool(
                            permissions.get("enableInternalTransfer") or permissions.get("permitsUniversalTransfer")
                            or permissions.get("enableUniversalTransfer")
                        )
                        result["permissions_verified"] = restriction_error is None
                        result["permission_check"] = restriction_error or "ok"
                        result["execution_ready"] = bool(
                            result["connected"] and result.get("trade_permission") is True
                            and restriction_error is None
                            and self.config.live_confirmed_by_user and self.config.live_worker_ready
                        )
            except Exception as e:
                result["connected"] = False
                result["account_check"] = str(e)
        else:
            result["connected"] = False
            result["note"] = "Configure the selected Binance environment API keys. Secrets remain server-side."
        return result

    def _validate_live_api_restrictions(self, restrictions: dict[str, Any]) -> str | None:
        """Fail closed unless Binance confirms IP allowlisting and least privilege."""
        if not isinstance(restrictions, dict) or restrictions.get("error"):
            return "live_api_permissions_unverified"
        if restrictions.get("ipRestrict") is not True:
            return "live_api_ip_allowlist_required"
        # Binance's documented apiRestrictions response exposes ipRestrict,
        # but does not guarantee returning the actual IP entries. Validate them
        # when present; otherwise require the restriction flag and surface a
        # manual API-Management verification note in status().
        if "ipList" in restrictions:
            ip_list = restrictions.get("ipList")
            if not isinstance(ip_list, list) or not ip_list or not all(isinstance(ip, str) and ip.strip() for ip in ip_list):
                return "live_api_ip_allowlist_empty_or_invalid"
        forbidden_flags = ("enableWithdrawals", "enableInternalTransfer", "permitsUniversalTransfer", "enableUniversalTransfer")
        if any(restrictions.get(flag) is True for flag in forbidden_flags):
            return "live_api_key_has_withdrawal_or_transfer_permissions"
        required_permission = "enableSpotAndMarginTrading" if self.config.market == "spot" else "enableFutures"
        if restrictions.get(required_permission) is not True:
            return "live_api_required_market_permission_missing"
        return None

    @staticmethod
    def _open_order_conduct_veto(side: str, order_type: str, price: float | None,
                                 open_orders: list[dict[str, Any]]) -> str | None:
        """Conservatively prevent self-matches and repeated same-side order layering."""
        requested_side = str(side).upper()
        opposite_side = "SELL" if requested_side == "BUY" else "BUY"
        requested_type = str(order_type).upper()
        for row in open_orders:
            if not isinstance(row, dict):
                return "open_order_state_invalid"
            existing_side = str(row.get("side", "")).upper()
            if existing_side == requested_side and requested_type == "LIMIT":
                return "repeated_same_side_limit_order_blocked"
            if existing_side != opposite_side:
                continue
            if requested_type == "MARKET":
                return "potential_self_trade_with_opposite_open_order"
            if requested_type == "LIMIT" and price is not None:
                try:
                    existing_price = float(row.get("price", 0) or 0)
                except (TypeError, ValueError, OverflowError):
                    return "open_order_price_unverifiable"
                if not math.isfinite(existing_price) or existing_price <= 0:
                    return "open_order_price_unverifiable"
                if ((requested_side == "BUY" and float(price) >= existing_price)
                        or (requested_side == "SELL" and float(price) <= existing_price)):
                    return "potential_self_trade_with_crossing_open_order"
        return None

    def _execution_ready(self) -> dict[str, Any] | None:
        if not self.config.enabled:
            return {"ok": False, "sent_to_exchange": False, "error": "binance_execution_disabled"}
        if self.config.environment not in {"testnet", "live"}:
            return {"ok": False, "sent_to_exchange": False, "error": "unsupported_binance_environment"}
        if self.config.market not in {"spot", "futures"}:
            return {"ok": False, "sent_to_exchange": False, "error": "unsupported_market"}
        if not self._api_key() or not self._api_secret():
            return {"ok": False, "sent_to_exchange": False,
                    "error": "live_credentials_missing" if self.config.environment == "live" else "testnet_credentials_missing"}
        if self.config.environment == "live":
            if not self.config.live_confirmed_by_user:
                return {"ok": False, "sent_to_exchange": False, "error": "live_requires_explicit_user_confirmation"}
            if not self.config.live_worker_ready:
                return {"ok": False, "sent_to_exchange": False, "error": "live_supervised_worker_required"}
        return None

    def _symbol_filters(self, symbol: str) -> dict[str, float]:
        info = self.get_exchange_info(symbol)
        if isinstance(info, dict) and info.get("error"):
            raise RuntimeError("exchange_info_unavailable")
        symbols = info.get("symbols", []) if isinstance(info, dict) else []
        target = symbol.replace("/", "").upper()
        row = next((x for x in symbols if str(x.get("symbol", "")).upper() == target), None)
        if not row or row.get("status") not in (None, "TRADING"):
            raise RuntimeError("symbol_not_trading_or_not_found")
        result: dict[str, float] = {}
        lot_step = 0.0
        market_step = 0.0
        for item in row.get("filters", []):
            kind = item.get("filterType")
            if kind == "LOT_SIZE":
                lot_step = float(item.get("stepSize", 0) or 0)
                result["minQty"] = float(item.get("minQty", 0) or 0)
                result["maxQty"] = float(item.get("maxQty", 0) or 0)
            elif kind == "MARKET_LOT_SIZE":
                market_step = float(item.get("stepSize", 0) or 0)
                result["marketMinQty"] = float(item.get("minQty", 0) or 0)
                result["marketMaxQty"] = float(item.get("maxQty", 0) or 0)
            elif kind == "PRICE_FILTER":
                result["minPrice"] = float(item.get("minPrice", 0) or 0)
                result["maxPrice"] = float(item.get("maxPrice", 0) or 0)
                result["tickSize"] = float(item.get("tickSize", 0) or 0)
            elif kind == "MIN_NOTIONAL":
                result["minNotional"] = float(item.get("minNotional", 0) or 0)
                result["applyMinToMarket"] = 1.0 if item.get("applyToMarket") else 0.0
                result["avgPriceMins"] = float(item.get("avgPriceMins", 0) or 0)
            elif kind == "NOTIONAL":
                result["minNotional"] = float(item.get("minNotional", 0) or 0)
                result["maxNotional"] = float(item.get("maxNotional", 0) or 0)
                result["applyMinToMarket"] = 1.0 if item.get("applyMinToMarket") else 0.0
                result["applyMaxToMarket"] = 1.0 if item.get("applyMaxToMarket") else 0.0
                result["avgPriceMins"] = float(item.get("avgPriceMins", 0) or 0)
            elif kind == "PERCENT_PRICE":
                result["percentMultiplierUp"] = float(item.get("multiplierUp", 0) or 0)
                result["percentMultiplierDown"] = float(item.get("multiplierDown", 0) or 0)
                result["avgPriceMins"] = float(item.get("avgPriceMins", 0) or 0)
            elif kind == "PERCENT_PRICE_BY_SIDE":
                result["bidMultiplierUp"] = float(item.get("bidMultiplierUp", 0) or 0)
                result["bidMultiplierDown"] = float(item.get("bidMultiplierDown", 0) or 0)
                result["askMultiplierUp"] = float(item.get("askMultiplierUp", 0) or 0)
                result["askMultiplierDown"] = float(item.get("askMultiplierDown", 0) or 0)
                result["avgPriceMins"] = float(item.get("avgPriceMins", 0) or 0)
            elif kind == "ICEBERG_PARTS":
                result["icebergPartsLimit"] = float(item.get("limit", 0) or 0)
            elif kind == "MAX_NUM_ORDERS":
                result["maxNumOrders"] = float(item.get("maxNumOrders", 0) or 0)
            elif kind == "MAX_POSITION":
                result["maxPosition"] = float(item.get("maxPosition", 0) or 0)
            elif kind == "MAX_NUM_ALGO_ORDERS":
                result["maxNumAlgoOrders"] = float(item.get("maxNumAlgoOrders", 0) or 0)
        for item in info.get("exchangeFilters", []) if isinstance(info, dict) else []:
            if item.get("filterType") == "EXCHANGE_MAX_NUM_ORDERS":
                result["exchangeMaxNumOrders"] = float(item.get("maxNumOrders", 0) or 0)
        # Prefer the market-lot step only for MARKET orders; the gateway
        # selects the appropriate quantity filter based on order type.
        result["stepSize"] = market_step or lot_step
        result["lotStepSize"] = lot_step
        result["marketStepSize"] = market_step or lot_step
        if not result.get("tickSize") and not result.get("stepSize"):
            raise RuntimeError("required_exchange_filters_missing")
        return result

    def _market_average_price(self, symbol: str) -> float:
        """Fetch Binance's documented weighted-average reference price."""
        path = "/fapi/v1/avgPrice" if self.config.market == "futures" else "/api/v3/avgPrice"
        result = self._request("GET", path, params={"symbol": symbol.replace("/", "")})
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError("market_average_price_unavailable")
        value = float(result.get("price", 0) or 0) if isinstance(result, dict) else 0.0
        if not math.isfinite(value) or value <= 0:
            raise RuntimeError("invalid_market_average_price")
        return value

    def _market_price(self, symbol: str) -> float:
        path = "/fapi/v1/ticker/price" if self.config.market == "futures" else "/api/v3/ticker/price"
        result = self._request("GET", path, params={"symbol": symbol.replace("/", "")})
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError("market_price_unavailable")
        value = float(result.get("price", 0) or 0) if isinstance(result, dict) else 0.0
        if value <= 0:
            raise RuntimeError("invalid_market_price")
        return value


    @staticmethod
    def _base_quote_assets(symbol: str) -> tuple[str, str]:
        normalized = symbol.replace("/", "").upper()
        if "/" in symbol:
            base, quote = symbol.upper().split("/", 1)
            return base, quote
        known_quotes = ("FDUSD", "USDT", "USDC", "BUSD", "TUSD", "BTC", "ETH", "BNB", "TRY", "EUR", "GBP", "BRL", "AUD", "JPY")
        for quote in known_quotes:
            if normalized.endswith(quote) and len(normalized) > len(quote):
                return normalized[:-len(quote)], quote
        raise RuntimeError("unable_to_resolve_spot_base_quote_assets")

    @staticmethod
    def _free_asset_balance(account: dict[str, Any], asset: str) -> float:
        balances = account.get("balances")
        if not isinstance(balances, list):
            raise RuntimeError("spot_account_balances_unavailable")
        row = next((item for item in balances if str(item.get("asset", "")).upper() == asset.upper()), None)
        if row is None:
            return 0.0
        return max(0.0, float(row.get("free", 0) or 0))

    def set_leverage(self, symbol: str, leverage: float) -> dict[str, Any]:
        """Set USD-M Futures Testnet leverage; TITAN hard cap is 10x."""
        blocked = self._execution_ready()
        if blocked:
            return blocked
        if self.config.market != "futures":
            return {"ok": False, "sent_to_exchange": False, "error": "futures_market_required"}
        try:
            lev = float(leverage)
        except (TypeError, ValueError):
            return {"ok": False, "sent_to_exchange": False, "error": "invalid_leverage"}
        if lev < 1 or lev > 10 or not lev.is_integer():
            return {"ok": False, "sent_to_exchange": False, "error": "leverage_outside_titan_cap", "max_leverage": 10}
        result = self._request("POST", "/fapi/v1/leverage", signed=True,
                               params={"symbol": symbol.replace("/", "").upper(), "leverage": int(lev)})
        return {"ok": isinstance(result, dict) and "error" not in result,
                "testnet": True, "response": result, "leverage_requested": int(lev)}

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> dict[str, Any]:
        """Require isolated USD-M Futures margin on Binance Testnet before opening orders."""
        blocked = self._execution_ready()
        if blocked:
            return blocked
        if self.config.environment != "testnet" or self.config.market != "futures":
            return {"ok": False, "sent_to_exchange": False, "error": "isolated_margin_testnet_futures_only"}
        normalized_symbol = symbol.replace("/", "").upper()
        requested = str(margin_type or "").strip().upper()
        if requested != "ISOLATED":
            return {"ok": False, "sent_to_exchange": False, "error": "titan_futures_margin_mode_must_be_isolated"}
        result = self._request(
            "POST", "/fapi/v1/marginType", signed=True,
            params={"symbol": normalized_symbol, "marginType": "ISOLATED"},
        )
        if isinstance(result, dict) and "error" not in result:
            return {"ok": True, "sent_to_exchange": True, "margin_type": "ISOLATED", "response": result}
        # Binance -4046 means the symbol is already in the requested margin mode.
        if isinstance(result, dict) and result.get("error") == "http_error":
            try:
                error_body = json.loads(str(result.get("body") or "{}"))
                if int(error_body.get("code")) == -4046:
                    return {"ok": True, "sent_to_exchange": False, "already_isolated": True,
                            "margin_type": "ISOLATED", "response": result}
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return {"ok": False, "sent_to_exchange": False, "error": "isolated_margin_setup_failed",
                "margin_type": "ISOLATED", "response": result}

    def place_order(self, symbol: str, side: str, order_type: str, quantity: float,
                    price: float | None = None, *, leverage: float = 1.0,
                    reduce_only: bool = False, idempotency_key: str | None = None,
                    market_conduct_flags: dict[str, Any] | None = None) -> dict[str, Any]:
        blocked = self._execution_ready()
        if blocked:
            return blocked
        market = MarketType.FUTURES if self.config.market == "futures" else MarketType.SPOT
        try:
            lev = float(leverage)
        except (TypeError, ValueError):
            return {"ok": False, "sent_to_exchange": False, "error": "invalid_leverage"}
        if market == MarketType.SPOT and (lev != 1.0 or reduce_only):
            return {"ok": False, "sent_to_exchange": False, "error": "spot_leverage_or_reduce_only_not_supported"}
        if order_type.upper() not in {"MARKET", "LIMIT"}:
            return {"ok": False, "sent_to_exchange": False, "error": "unsupported_order_type"}
        if market == MarketType.FUTURES and (lev < 1 or lev > 10 or not lev.is_integer()):
            return {"ok": False, "sent_to_exchange": False, "error": "leverage_outside_titan_cap", "max_leverage": 10, "integer_leverage_required": True}
        account = self.get_account()
        if not isinstance(account, dict) or account.get("error") or account.get("canTrade") is not True:
            return {"ok": False, "sent_to_exchange": False, "error": "testnet_account_not_tradeable", "details": account}
        try:
            filters = self._symbol_filters(symbol)
            # The limit price is never a valid substitute for the market reference
            # price. Binance percent/notional filters use the exchange reference
            # or weighted average price, not the submitted order price.
            reference = self._market_price(symbol)
            if float(filters.get("avgPriceMins", 0) or 0) > 0:
                reference = self._market_average_price(symbol)
        except Exception as exc:
            return {"ok": False, "sent_to_exchange": False, "error": "market_metadata_unavailable", "detail": str(exc)}
        current_position = None
        current_symbol_open_orders = None
        current_exchange_open_orders = None
        needs_exchange_orders = bool(filters.get("exchangeMaxNumOrders"))
        # Always inspect this symbol's live open orders before placing another
        # order. This prevents same-side order stacking and potential self-matches.
        open_orders = self.get_open_orders(None if needs_exchange_orders else symbol)
        if not isinstance(open_orders, list):
            return {"ok": False, "sent_to_exchange": False, "error": "open_order_state_unavailable"}
        normalized_symbol = symbol.replace("/", "").upper()
        symbol_orders = [row for row in open_orders if str(row.get("symbol", "")).upper() == normalized_symbol]
        current_symbol_open_orders = len(symbol_orders)
        current_exchange_open_orders = len(open_orders) if needs_exchange_orders else None
        conduct_veto = self._open_order_conduct_veto(side, order_type, price, symbol_orders)
        if conduct_veto:
            return {"ok": False, "sent_to_exchange": False, "error": conduct_veto}
        if filters.get("maxPosition") and market == MarketType.SPOT and side.lower() == "buy":
                try:
                    base_asset, _ = self._base_quote_assets(symbol)
                    balances = account.get("balances") or []
                    balance_row = next((row for row in balances if str(row.get("asset", "")).upper() == base_asset), {})
                    current_position = float(balance_row.get("free", 0) or 0) + float(balance_row.get("locked", 0) or 0)
                    for row in symbol_orders:
                        if str(row.get("side", "")).upper() == "BUY":
                            remaining = float(row.get("origQty", 0) or 0) - float(row.get("executedQty", 0) or 0)
                            current_position += max(0.0, remaining)
                except (TypeError, ValueError, OverflowError):
                    return {"ok": False, "sent_to_exchange": False, "error": "position_filter_state_unavailable"}
        pre = self.gateway.validate(
            symbol=symbol, market=market, side=side.lower(), order_type=order_type.lower(),
            quantity=quantity, price=price, reference_price=reference,
            environment_mode="live" if self.config.environment == "live" else "demo",
            live_enabled=self.config.environment == "live" and self.config.live_confirmed_by_user,
            idempotency_key=idempotency_key,
            # Binance's authenticated account and API-restriction checks are the
            # source of truth for market eligibility. The adapter's own shared
            # 418/429 cooldown is the active rate-limit control.
            geo_verified=None,
            kyc_verified=None,
            rate_limit_ok=True if self.config.environment == "live" else None,
            reduce_only=reduce_only, exchange_filters=filters,
            market_conduct_flags=market_conduct_flags,
            current_position=current_position,
            current_open_orders_count=current_symbol_open_orders,
            current_exchange_open_orders_count=current_exchange_open_orders,
        )
        if not pre.ok:
            return {"ok": False, "sent_to_exchange": False, "error": "pretrade_blocked", "details": pre.to_dict()}
        if market == MarketType.SPOT:
            try:
                base_asset, quote_asset = self._base_quote_assets(symbol)
                if side.lower() == "buy":
                    asset = quote_asset
                    required_balance = float(pre.normalized.get("notional_estimate") or 0.0) * 1.001
                elif side.lower() == "sell":
                    asset = base_asset
                    required_balance = float(quantity)
                else:
                    return {"ok": False, "sent_to_exchange": False, "error": "invalid_spot_side"}
                available_balance = self._free_asset_balance(account, asset)
            except Exception as exc:
                return {"ok": False, "sent_to_exchange": False, "error": "spot_balance_check_unavailable", "detail": str(exc)}
            if available_balance + 1e-12 < required_balance:
                return {"ok": False, "sent_to_exchange": False, "error": "insufficient_testnet_spot_balance",
                        "asset": asset, "available": available_balance, "required": round(required_balance, 12)}
        if self.config.environment == "live":
            permissions = self._request("GET", "/sapi/v1/account/apiRestrictions", signed=True)
            restriction_error = self._validate_live_api_restrictions(permissions)
            if restriction_error:
                return {"ok": False, "sent_to_exchange": False, "error": restriction_error}
        if market == MarketType.FUTURES and not reduce_only:
            # Opening exposure requires isolated margin and an exchange leverage
            # setting. A reduce-only close must remain possible even when an
            # existing manually-created position is Cross or available margin is low.
            available = float(account.get("availableBalance", 0) or 0)
            required_margin = float(pre.normalized.get("notional_estimate") or 0) / lev
            if available < required_margin * 1.10:
                return {"ok": False, "sent_to_exchange": False, "error": "insufficient_testnet_margin",
                        "available_balance": available, "estimated_initial_margin_with_buffer": round(required_margin * 1.10, 8)}
            margin_result = self.set_margin_type(symbol, "ISOLATED")
            if not margin_result.get("ok"):
                return {"ok": False, "sent_to_exchange": False, "error": "isolated_margin_setup_failed",
                        "details": margin_result}
            lev_result = self.set_leverage(symbol, lev)
            if not lev_result.get("ok"):
                return {"ok": False, "sent_to_exchange": False, "error": "leverage_setup_failed", "details": lev_result}
        if self.config.environment == "live" and (not idempotency_key or len(idempotency_key.strip()) < 8):
            return {"ok": False, "sent_to_exchange": False, "error": "live_idempotency_key_required"}
        # Binance client order IDs are limited to 36 characters. Derive a
        # stable, compact ID for both Testnet and Live so reconciliation can
        # identify an order even when the submission response is lost.
        client_order_id = "titan" + hashlib.sha256(
            (idempotency_key or uuid.uuid4().hex).encode("utf-8")
        ).hexdigest()[:28]
        params: dict[str, Any] = {
            "symbol": symbol.replace("/", "").upper(),
            "newClientOrderId": client_order_id,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": format(float(quantity), ".12f").rstrip("0").rstrip("."),
        }
        if market == MarketType.SPOT:
            # FULL response includes per-fill commission and commission asset.
            params["newOrderRespType"] = "FULL"
        if order_type.upper() == "LIMIT":
            if price is None:
                return {"ok": False, "sent_to_exchange": False, "error": "limit_price_required"}
            params["price"] = format(float(price), ".12f").rstrip("0").rstrip(".")
            params["timeInForce"] = "GTC"
        if market == MarketType.FUTURES and reduce_only:
            params["reduceOnly"] = "true"
        endpoint = "/fapi/v1/order" if market == MarketType.FUTURES else "/api/v3/order"
        res = self._request("POST", endpoint, signed=True, params=params)
        ok = isinstance(res, dict) and "error" not in res
        fee_accounting = None
        if ok and res.get("orderId") is not None:
            try:
                if float(res.get("executedQty", 0) or 0) > 0:
                    fee_accounting = self.get_order_fee_quote(
                        symbol, res.get("orderId"), fills=res.get("fills"),
                    )
            except (TypeError, ValueError, OverflowError):
                fee_accounting = {"ok": False, "pending": True, "error": "invalid_order_fill_for_fee_accounting"}
        return {"ok": ok, "sent_to_exchange": ok, "testnet": self.config.environment == "testnet",
                "live": self.config.environment == "live", "market": market.value,
                "response": res, "fee_accounting": fee_accounting,
                "precheck": pre.to_dict(), "leverage": int(lev) if market == MarketType.FUTURES else 1}

    def close_position(self, symbol: str, side: str, quantity: float, *, leverage: float = 1.0,
                       idempotency_key: str | None = None) -> dict[str, Any]:
        position_side = str(side or "").strip().upper()
        if position_side in {"BUY", "LONG", "LONG_SPOT"}:
            close_side = "SELL"
        elif position_side in {"SELL", "SHORT"}:
            close_side = "BUY"
        else:
            return {"ok": False, "sent_to_exchange": False, "error": "invalid_position_side_for_close"}
        return self.place_order(symbol, close_side, "MARKET", quantity, leverage=leverage,
                                reduce_only=self.config.market == "futures",
                                idempotency_key=idempotency_key)

    def get_account(self) -> dict[str, Any]:
        if self.config.market == "futures":
            return self._request("GET", "/fapi/v2/account", signed=True)
        return self._request("GET", "/api/v3/account", signed=True)

    def get_open_orders(self, symbol: str | None = None) -> dict[str, Any]:
        params = {}
        if symbol:
            params["symbol"] = symbol.replace("/", "")
        if self.config.market == "futures":
            return self._request("GET", "/fapi/v1/openOrders", signed=True, params=params)
        return self._request("GET", "/api/v3/openOrders", signed=True, params=params)

    def get_order(self, symbol: str, *, client_order_id: str | None = None,
                  order_id: str | int | None = None) -> dict[str, Any]:
        """Read-only status lookup used to reconcile an uncertain/pending order."""
        if not client_order_id and order_id is None:
            return {"error": "order_identifier_required"}
        params: dict[str, Any] = {"symbol": symbol.replace("/", "").upper()}
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        else:
            params["orderId"] = order_id
        path = "/fapi/v1/order" if self.config.market == "futures" else "/api/v3/order"
        return self._request("GET", path, signed=True, params=params)

    def get_order_fee_quote(self, symbol: str, order_id: str | int, *,
                            fills: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Return commissions converted to the order's quote asset for risk accounting."""
        if order_id is None:
            return {"ok": False, "pending": True, "error": "fee_order_id_required"}
        trades = fills if isinstance(fills, list) and fills else None
        if trades is None:
            path = "/fapi/v1/userTrades" if self.config.market == "futures" else "/api/v3/myTrades"
            trades = self._request("GET", path, signed=True, params={
                "symbol": symbol.replace("/", "").upper(), "orderId": order_id,
            })
        if not isinstance(trades, list) or not trades:
            return {"ok": False, "pending": True, "error": "order_fee_history_unavailable"}
        try:
            base_asset, quote_asset = self._base_quote_assets(symbol)
            total_quote = 0.0
            breakdown: dict[str, float] = {}
            for trade in trades:
                if not isinstance(trade, dict):
                    raise ValueError("invalid_trade_fee_record")
                commission = float(trade.get("commission", 0) or 0)
                asset = str(trade.get("commissionAsset") or "").upper()
                if not math.isfinite(commission) or commission < 0 or (commission > 0 and not asset):
                    raise ValueError("invalid_trade_commission")
                if commission == 0:
                    continue
                if asset == quote_asset:
                    quote_fee = commission
                elif asset == base_asset:
                    fill_price = float(trade.get("price", 0) or 0)
                    if not math.isfinite(fill_price) or fill_price <= 0:
                        raise ValueError("base_asset_fee_price_unavailable")
                    quote_fee = commission * fill_price
                else:
                    conversion_price = self._market_price(f"{asset}{quote_asset}")
                    quote_fee = commission * conversion_price
                if not math.isfinite(quote_fee) or quote_fee < 0:
                    raise ValueError("invalid_quote_fee_conversion")
                total_quote += quote_fee
                breakdown[asset] = breakdown.get(asset, 0.0) + commission
            quote_to_usdt = 1.0 if quote_asset == "USDT" else self._market_price(f"{quote_asset}USDT")
            fees_usdt = total_quote * quote_to_usdt
            if not math.isfinite(fees_usdt) or fees_usdt < 0:
                raise ValueError("invalid_usdt_fee_conversion")
            return {
                "ok": True, "pending": False,
                "fees_paid_usdt": fees_usdt, "fee_asset": "USDT",
                "quote_asset": quote_asset, "fee_breakdown": breakdown,
                "trade_count": len(trades),
            }
        except Exception as exc:
            return {"ok": False, "pending": True, "error": str(exc)[:160]}

    def cancel_order(self, symbol: str, *, order_id: str | int | None = None,
                     client_order_id: str | None = None) -> dict[str, Any]:
        """Cancel one known exchange order; never submits a replacement order."""
        blocked = self._execution_ready()
        if blocked:
            return blocked
        if order_id is None and not client_order_id:
            return {"ok": False, "sent_to_exchange": False, "error": "order_identifier_required"}
        params: dict[str, Any] = {"symbol": symbol.replace("/", "").upper()}
        if order_id is not None:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        path = "/fapi/v1/order" if self.config.market == "futures" else "/api/v3/order"
        response = self._request("DELETE", path, signed=True, params=params)
        ok = isinstance(response, dict) and "error" not in response
        return {"ok": ok, "sent_to_exchange": ok, "response": response,
                "testnet": self.config.environment == "testnet",
                "live": self.config.environment == "live"}

    def get_exchange_info(self, symbol: str | None = None) -> dict[str, Any]:
        params = {}
        if symbol:
            params["symbol"] = symbol.replace("/", "")
        if self.config.market == "futures":
            return self._request("GET", "/fapi/v1/exchangeInfo", signed=False, params=params)
        return self._request("GET", "/api/v3/exchangeInfo", signed=False, params=params)

    def reconcile(self, local_open_orders: list[dict] | None = None) -> dict[str, Any]:
        """Read-only reconciliation: fetch remote open orders and compare with local state."""
        remote = self.get_open_orders()
        if isinstance(remote, dict) and remote.get("error"):
            return {"ok": False, "error": "remote_fetch_failed", "details": remote, "action": "halt_new_orders_until_resolved"}
        remote_list = remote if isinstance(remote, list) else []
        local = local_open_orders or []
        # Match either exchange order ID or client order ID. Binance responses
        # commonly contain both; preferring only one creates false mismatches.
        local_ids: set[str] = set()
        for order in local:
            for key in ("id", "orderId", "clientOrderId"):
                value = order.get(key)
                if value is not None:
                    local_ids.add(str(value))
        remote_ids: set[str] = set()
        for order in remote_list:
            for key in ("orderId", "clientOrderId"):
                value = order.get(key)
                if value is not None:
                    remote_ids.add(str(value))
        missing_local = [
            order for order in remote_list
            if not any(str(order.get(key)) in local_ids for key in ("orderId", "clientOrderId") if order.get(key) is not None)
        ]
        missing_remote = [
            order for order in local
            if not any(str(order.get(key)) in remote_ids for key in ("id", "orderId", "clientOrderId") if order.get(key) is not None)
        ]
        ok = len(missing_local) == 0 and len(missing_remote) == 0
        return {
            "ok": ok,
            "remote_open_orders_count": len(remote_list),
            "local_open_orders_count": len(local),
            "missing_in_local": len(missing_local),
            "missing_in_remote": len(missing_remote),
            "action": "ok" if ok else "review_required_halt_new_orders",
            "details": {"missing_in_local_sample": missing_local[:3], "missing_in_remote_sample": missing_remote[:3]},
        }

    # Compatibility methods for existing tests/workflows
    def precheck_order(self, **kwargs) -> dict[str, Any]:
        decision = self.gateway.validate(**kwargs)
        return decision.to_dict() if hasattr(decision, 'to_dict') else decision

    def place_order_shadow(self, **kwargs) -> dict[str, Any]:
        pre = self.precheck_order(**kwargs)
        return {"mode": "shadow", "sent_to_exchange": False, "precheck": pre}

    def place_order_live(self, **kwargs) -> dict[str, Any]:
        """Explicit Live execution entrypoint; still guarded by config and user confirmation."""
        if self.config.environment != "live":
            return {"ok": False, "sent_to_exchange": False, "error": "live_adapter_environment_required"}
        return self.place_order(**kwargs)


class BinanceLiveAdapter(BinanceTestnetAdapter):
    """Explicit Live adapter type; cannot be instantiated with Testnet settings."""
    name = "binance_live"

    def __init__(self, config: BinanceConfig | None = None):
        selected = config or BinanceConfig(environment="live")
        if selected.environment != "live":
            raise ValueError("BinanceLiveAdapter_requires_live_environment")
        super().__init__(selected)


# Keep old names for compatibility
BinanceAdapter = BinanceTestnetAdapter
BinanceConfig = BinanceConfig
