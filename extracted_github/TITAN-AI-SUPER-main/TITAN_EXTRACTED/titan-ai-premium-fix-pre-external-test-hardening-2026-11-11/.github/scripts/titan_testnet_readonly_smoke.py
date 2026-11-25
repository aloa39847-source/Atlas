#!/usr/bin/env python3
"""Read-only Binance Spot Testnet smoke test. This script cannot submit orders."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

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

    # enabled=False is deliberate: even accidental future calls to place_order
    # remain blocked. This smoke test performs GET requests only.
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

    exchange_info = adapter.get_exchange_info(symbol)
    if not isinstance(exchange_info, dict) or exchange_info.get("error"):
        report["testnet_exchange_info"] = {"ok": False, "error": "exchange_info_unavailable"}
        emit(report)
        return 1
    symbols = exchange_info.get("symbols")
    match = [item for item in symbols or [] if isinstance(item, dict) and item.get("symbol") == symbol]
    if len(match) != 1 or match[0].get("status") != "TRADING":
        report["testnet_exchange_info"] = {"ok": False, "error": "symbol_missing_ambiguous_or_not_trading"}
        emit(report)
        return 1
    report["testnet_exchange_info"] = {
        "ok": True, "symbol": symbol, "status": match[0].get("status"),
        "base_asset": match[0].get("baseAsset"), "quote_asset": match[0].get("quoteAsset"),
    }

    if credentials_present:
        status = adapter.status()
        connected = bool(status.get("connected"))
        report["testnet_account_auth"] = {
            "ok": connected,
            "account_check": status.get("account_check"),
            "trading_allowed": status.get("trading_allowed"),
            "withdrawal_operations_supported": False,
        }
        if not connected:
            emit(report)
            return 1
    else:
        report["testnet_account_auth"] = {
            "ok": None, "skipped": True,
            "reason": "Add BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET as GitHub Actions secrets to verify private account access.",
        }

    # Analysis uses current public Binance market data (read-only), while account
    # authentication and symbol metadata above are checked against Spot Testnet.
    settings.binance_public_data_enabled = True
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    try:
        with Session(engine, autoflush=False, autocommit=False) as db:
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
    finally:
        engine.dispose()

    ok = bool(report["testnet_exchange_info"]["ok"]
              and (report["testnet_account_auth"]["ok"] is not False)
              and report.get("market_data", {}).get("ok")
              and report.get("analysis", {}).get("ok"))
    report["ok"] = ok
    report["next_stage"] = "testnet_order_lifecycle_requires_a_separate_explicitly_approved_workflow"
    emit(report)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
