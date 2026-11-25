#!/usr/bin/env python3
"""Read-only USD-M Futures Testnet preflight. This module has no order methods."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://testnet.binancefuture.com"


class PreflightError(RuntimeError):
    pass


def request(path: str, params: dict[str, str] | None = None, *, signed: bool = False):
    key = os.environ["BINANCE_TESTNET_FUTURES_API_KEY"]
    secret = os.environ["BINANCE_TESTNET_FUTURES_API_SECRET"]
    values = dict(params or {})
    if signed:
        values["timestamp"] = str(int(time.time() * 1000))
        values["recvWindow"] = "5000"
        query = urllib.parse.urlencode(values)
        values["signature"] = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    query = urllib.parse.urlencode(values)
    req = urllib.request.Request(
        BASE + path + (("?" + query) if query else ""),
        headers={"X-MBX-APIKEY": key},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise PreflightError(f"Futures Testnet read-only request failed: HTTP {exc.code}: {body[:300]}") from exc
    except Exception as exc:
        raise PreflightError(f"Futures Testnet read-only request uncertain: {type(exc).__name__}") from exc


def main() -> int:
    # Reject accidental presence of any production credential namespace.
    live_names = [name for name in os.environ if name.startswith("BINANCE_LIVE_")]
    if live_names:
        raise PreflightError("Production credential variables are forbidden in this workflow")
    key = os.getenv("BINANCE_TESTNET_FUTURES_API_KEY", "")
    secret = os.getenv("BINANCE_TESTNET_FUTURES_API_SECRET", "")
    if not key or not secret:
        raise PreflightError("Dedicated Futures Testnet API key/secret are required")
    if os.getenv("BINANCE_TESTNET_MARKET", "futures").lower() != "futures":
        raise PreflightError("BINANCE_TESTNET_MARKET must explicitly equal futures")
    symbol = os.getenv("TITAN_FUTURES_TESTNET_SYMBOL", "BTCUSDT").upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{5,20}", symbol):
        raise PreflightError("Invalid Futures symbol")

    # Only GET requests to the fixed Futures Testnet host are present.
    exchange = request("/fapi/v1/exchangeInfo")
    matches = [item for item in exchange.get("symbols", []) if item.get("symbol") == symbol]
    if len(matches) != 1 or matches[0].get("status") != "TRADING":
        raise PreflightError(f"{symbol} is not tradable on USD-M Futures Testnet")
    account = request("/fapi/v2/account", signed=True)
    positions = request("/fapi/v2/positionRisk", signed=True)
    if not isinstance(positions, list):
        raise PreflightError("Unexpected Futures Testnet position response")
    symbol_positions = [p for p in positions if p.get("symbol") == symbol]
    print(json.dumps({
        "ok": True,
        "environment": "USD_M_FUTURES_TESTNET",
        "base_url": BASE,
        "symbol": symbol,
        "can_trade": bool(account.get("canTrade")),
        "available_balance": account.get("availableBalance"),
        "position_rows_for_symbol": len(symbol_positions),
        "open_position_amounts": [p.get("positionAmt") for p in symbol_positions],
        "orders_submitted": False,
        "live_trading": False,
        "endpoints": ["GET /fapi/v1/exchangeInfo", "GET /fapi/v2/account", "GET /fapi/v2/positionRisk"],
    }, sort_keys=True))
    if not account.get("canTrade"):
        raise PreflightError("Futures Testnet account is not enabled for trading")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PreflightError as exc:
        print(json.dumps({"ok": False, "environment": "USD_M_FUTURES_TESTNET",
                          "error": str(exc), "orders_submitted": False}), file=sys.stderr)
        raise SystemExit(1)
