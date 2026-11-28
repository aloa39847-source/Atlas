#!/usr/bin/env python3
"""Retired UI patch helper; intentionally disabled.

This legacy helper targets an obsolete project path and instructs users to put
Binance Testnet credentials in environment variables. The current TITAN
dashboard stores market-specific Testnet credentials through the authenticated,
encrypted credential workflow. Do not run automated UI patch scripts.
"""

raise SystemExit(
    "DISABLED: obsolete UI patch helper. Configure Spot/Futures Testnet keys "
    "only through the authenticated TITAN dashboard."
)
