#!/usr/bin/env python3
"""Run TITAN's supervised Spot execution-path regression tests.

This intentionally does not call Binance REST directly. It exercises TITAN's
BinanceExecutionService, durable OrderRecord idempotency, partial-fill storage,
and uncertain-outcome emergency-stop behavior with a deterministic fake adapter.
Real Spot Testnet orders remain a separately gated manual stage.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[2]
    backend = root / "titan_project" / "titan-ai-premium-78d9310021b58c510c8362e2acb34eba6353eebc" / "backend"
    if not (backend / "app" / "services" / "binance_execution.py").is_file():
        raise SystemExit("TITAN backend execution service not found; refusing standalone exchange execution")
    tests = backend / "tests" / "test_live_margin_and_trading_pause.py"
    if not tests.is_file():
        raise SystemExit("TITAN execution regression tests not found")
    command = [
        sys.executable, "-m", "pytest", "-q", str(tests),
        "-k", "execution_partial_fill_is_durably_recorded or uncertain_execution_outcome_latches",
    ]
    print("Running TITAN supervised execution boundary tests; no Binance REST request or order will be sent.")
    return subprocess.call(command, cwd=backend)


if __name__ == "__main__":
    raise SystemExit(main())
