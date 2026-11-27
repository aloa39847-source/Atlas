#!/usr/bin/env python3
"""Spot Testnet permission smoke test. It validates TRADE scope via order/test only; no order is submitted."""
from __future__ import annotations

import json
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.integrations.binance_adapter import BinanceConfig, BinanceTestnetAdapter
from app.models.db import Base
from app.models import entities  # noqa: F401
from app.services.core_service import core_service


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def main() -> int:
    symbol = os.getenv("TITAN_TESTNET_SYMBOL", "BTCUSDT").strip().upper()
    if not symbol or not symbol.isalnum():
        emit({"ok": False, "error": "invalid_symbol"})
        return 2

    # Actual order submission stays disabled. The only TRADE-scope check below
    # uses Binance's order/test endpoint, which validates parameters without
    # sending an order into the matching engine.
    credentials_present = bool(
        os.getenv("BINANCE_TESTNET_API_KEY")
        and os.getenv("BINANCE_TESTNET_API_SECRET")
    )
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=False,
        environment="testnet",
        market="spot",
        api_key_ref="env:BINANCE_TESTNET_API_KEY" if credentials_present else None,
        api_secret_ref="env:BINANCE_TESTNET_API_SECRET" if credentials_present else None,
    ))

    report = {
        "environment": "binance_spot_testnet",
        "symbol": symbol,
        "order_submission_enabled": False,
        "live_trading_enabled": False,
        "credentials_present": credentials_present,
    }

    # Check private account authentication FIRST. Use the adapter's signed
    # account GET directly so its low-level Binance error is not collapsed into
    # a generic status flag.
    if credentials_present:
        try:
            account = adapter._request("GET", "/api/v3/account", signed=True)
        except Exception as exc:
            account = {"error": type(exc).__name__, "message": str(exc)[:400]}

        account_error = None
        if isinstance(account, dict):
            account_error = account.get("error")
            if account_error is None and isinstance(account.get("code"), int) and account["code"] < 0:
                account_error = "binance_api_error"
        else:
            account_error = "invalid_response_type"

        account_ok = (
            isinstance(account, dict)
            and account_error is None
            and isinstance(account.get("balances"), list)
            and isinstance(account.get("canTrade"), bool)
        )
        diagnostic = {}
        if isinstance(account, dict):
            for field in ("error", "status", "code", "msg", "message"):
                value = account.get(field)
                if value is not None:
                    diagnostic[field] = str(value)[:500]
            body = account.get("body")
            if isinstance(body, str) and body:
                try:
                    parsed_body = json.loads(body)
                    if isinstance(parsed_body, dict):
                        for field in ("code", "msg"):
                            if parsed_body.get(field) is not None:
                                diagnostic[field] = str(parsed_body[field])[:500]
                except (TypeError, ValueError):
                    diagnostic["body"] = body[:500]

        if account_ok:
            account_check = "ok"
        elif diagnostic.get("status") == "451" or "restricted location" in diagnostic.get("msg", "").lower():
            # Binance has rejected the runner's location before credentials can
            # be validated. Do not mislabel this as an invalid API key.
            account_check = "binance_location_restricted_auth_not_verified"
        elif diagnostic.get("status") in {"401", "403"} or diagnostic.get("code") == "-2015":
            account_check = "credentials_rejected_or_permission_denied"
        else:
            account_check = "account_request_failed_or_invalid_payload"

        report["testnet_account_auth"] = {
            "ok": account_ok,
            "account_check": account_check,
            "authentication_verified": True if account_ok else None,
            "account_can_trade": account.get("canTrade") is True if account_ok else None,
            "trade_permission": None,
            "api_key_trade_permission_verified": None,
            "execution_ready": False,
            "execution_readiness_check": (
                "account_authentication_not_verified" if not account_ok else
                "api_key_trade_permission_requires_test_order_validation"
                if account.get("canTrade") is True else "binance_account_trading_disabled"
            ),
            "withdrawal_operations_supported": False,
            "diagnostic": diagnostic or ({"error": account_error} if account_error else {}),
        }
        if account_ok and account.get("canTrade") is True:
            validation = adapter.validate_testnet_trade_permission(symbol)
            report["testnet_trade_permission_validation"] = validation
            verified = bool(validation.get("ok") and validation.get("verified"))
            report["testnet_account_auth"]["trade_permission"] = True if verified else None
            report["testnet_account_auth"]["api_key_trade_permission_verified"] = True if verified else None
            report["testnet_account_auth"]["execution_ready"] = verified
            report["testnet_account_auth"]["execution_readiness_check"] = (
                "ok" if verified else validation.get("error", "test_order_validation_failed")
            )
        else:
            report["testnet_trade_permission_validation"] = {
                "ok": False, "skipped": True,
                "reason": "account authentication and canTrade must be verified first",
                "order_submitted": False,
            }
    else:
        account_ok = False
        report["testnet_account_auth"] = {
            "ok": None,
            "skipped": True,
            "reason": "GitHub Actions Testnet API key/secret are not both configured.",
        }

    # Public market metadata is an independent check. Its failure must not hide
    # the signed account-authentication result above.
    exchange_info = adapter.get_exchange_info(symbol)
    if not isinstance(exchange_info, dict) or exchange_info.get("error"):
        detail = exchange_info if isinstance(exchange_info, dict) else {"response_type": type(exchange_info).__name__}
        report["testnet_exchange_info"] = {
            "ok": False,
            "error": "exchange_info_unavailable",
            "detail": detail,
        }
    else:
        symbols = exchange_info.get("symbols")
        match = [
            item for item in symbols or []
            if isinstance(item, dict) and item.get("symbol") == symbol
        ]
        valid_symbol = len(match) == 1 and match[0].get("status") == "TRADING"
        report["testnet_exchange_info"] = {
            "ok": valid_symbol,
            "symbol": symbol if valid_symbol else None,
            "status": match[0].get("status") if len(match) == 1 else None,
            "base_asset": match[0].get("baseAsset") if len(match) == 1 else None,
            "quote_asset": match[0].get("quoteAsset") if len(match) == 1 else None,
            "error": None if valid_symbol else "symbol_missing_ambiguous_or_not_trading",
        }

    # Futures metadata is checked independently using its official Testnet
    # endpoint. No Futures credentials or trading permissions are inferred.
    futures_adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=False, environment="testnet", market="futures",
    ))
    futures_info = futures_adapter.get_exchange_info(symbol)
    futures_symbols = futures_info.get("symbols") if isinstance(futures_info, dict) else None
    futures_match = [
        item for item in futures_symbols or []
        if isinstance(item, dict) and item.get("symbol") == symbol
    ]
    futures_valid = len(futures_match) == 1 and futures_match[0].get("status") == "TRADING"
    report["futures_testnet_exchange_info"] = {
        "ok": futures_valid,
        "base_url": futures_adapter._base_url(),
        "symbol": symbol if futures_valid else None,
        "status": futures_match[0].get("status") if len(futures_match) == 1 else None,
        "error": None if futures_valid else "futures_testnet_symbol_missing_or_not_trading",
        "account_auth_verified": False,
        "note": "Public Futures Testnet endpoint only; separate Futures API credentials are not tested here.",
    }

    # Analysis is another independent read-only check. Capture failures rather
    # than losing the already-collected account-auth result.
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    try:
        with Session(engine, autoflush=False, autocommit=False) as db:
            try:
                analysis = core_service.analyze(
                    db, symbol=symbol, market_type="spot", environment_mode="demo",
                    data_source="binance_public",
                )
                report["market_data"] = {
                    "ok": analysis.get("data_source") == "binance_public",
                    "source": analysis.get("data_source"),
                    "timeframes": analysis.get("timeframes"),
                }
                report["analysis"] = {
                    "ok": bool(analysis.get("recorded")),
                    "action": analysis.get("action"),
                    "confidence": analysis.get("confidence"),
                    "decision_id": analysis.get("decision_id"),
                    "entry_price": analysis.get("entry_price"),
                    "stop_loss": analysis.get("stop_loss"),
                    "take_profit": analysis.get("take_profit"),
                }
            except Exception as exc:
                report["market_data"] = {"ok": False, "error": str(exc)[:300]}
                report["analysis"] = {"ok": False, "error": str(exc)[:300]}
    finally:
        engine.dispose()

    checks = [
        report["testnet_account_auth"].get("ok") is True,
        report.get("testnet_trade_permission_validation", {}).get("ok") is True,
        report["testnet_exchange_info"].get("ok") is True,
        report["futures_testnet_exchange_info"].get("ok") is True,
        report.get("market_data", {}).get("ok") is True,
        report.get("analysis", {}).get("ok") is True,
    ]
    report["ok"] = all(checks)
    location_restricted = (
        report["testnet_account_auth"].get("account_check")
        == "binance_location_restricted_auth_not_verified"
    )
    report["external_access_blocked"] = location_restricted
    report["order_test_endpoint_called"] = bool(report.get("testnet_trade_permission_validation", {}).get("validation_request_sent"))
    report["order_submitted"] = False
    report["matching_engine_reached"] = False
    report["next_stage"] = (
        "spot_testnet_order_lifecycle_requires_separate_explicit_approval"
        if report.get("testnet_trade_permission_validation", {}).get("ok")
        else "enable_testnet_api_key_trade_permission_then_repeat_order_test_validation"
    )
    emit(report)
    # External access restrictions are a failed live-verification gate.
    # They must never be converted into a successful CI result: authentication
    # remains unverified and the exchange integration is not validated.
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
