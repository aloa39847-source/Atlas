#!/usr/bin/env python3
"""Read-only Binance Spot Testnet smoke test. This script cannot submit orders."""
from __future__ import annotations

import json
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
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

    # Execution stays disabled. status() performs a signed GET only; no order
    # endpoint is called anywhere in this diagnostic.
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
    # a generic status flag. This is read-only; no order endpoint is called.
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

        report["testnet_account_auth"] = {
            "ok": account_ok,
            "account_check": "ok" if account_ok else "invalid_or_error_account_payload",
            "trade_permission": account.get("canTrade") if account_ok else None,
            "execution_ready": account_ok and account.get("canTrade") is True,
            "withdrawal_permissions": False,
            "diagnostic": diagnostic or ({"error": account_error} if account_error else {}),
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

    # Analysis is another independent read-only check. Capture failures rather
    # than losing the already-collected account-auth result.
    settings.binance_public_data_enabled = True
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    try:
        with Session(engine, autoflush=False, autocommit=False) as db:
            try:
                analysis = core_service.analyze(
                    db, symbol=symbol, market_type="spot", environment_mode="demo",
                )
                report["market_data"] = {
                    "ok": analysis.get("market_price_source") == "live_public",
                    "source": analysis.get("market_price_source"),
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
        report["testnet_exchange_info"].get("ok") is True,
        report.get("market_data", {}).get("ok") is True,
        report.get("analysis", {}).get("ok") is True,
    ]
    report["ok"] = all(checks)
    report["next_stage"] = "testnet_order_lifecycle_requires_a_separate_explicitly_approved_workflow"
    emit(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
