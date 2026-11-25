from __future__ import annotations

import pytest

from app.services import binance_trading_worker


def _control(target: str, confirmed: bool = False):
    return {
        "account_target": target,
        "environment_mode": "live" if target == "live" else "demo",
        "market_type": "spot",
        "bot_run_state": "starting",
        "emergency_stop": False,
        "live_confirmed_by_user": confirmed,
        "risk_limits": {},
        "trading_paused": False,
    }


def test_testnet_worker_passes_policy_gate_and_reaches_preflight(monkeypatch):
    monkeypatch.setattr(binance_trading_worker, "_read_control", lambda: _control("testnet"))
    monkeypatch.setattr(
        binance_trading_worker.dbmod,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("Testnet worker must proceed to account preflight")),
    )
    with pytest.raises(AssertionError, match="proceed to account preflight"):
        binance_trading_worker.run(interval_seconds=60)


def test_live_worker_requires_explicit_user_confirmation_before_any_exchange_access(monkeypatch):
    monkeypatch.setattr(binance_trading_worker, "_read_control", lambda: _control("live", False))
    monkeypatch.setattr(
        binance_trading_worker.dbmod,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("No database/exchange access before explicit approval")),
    )
    with pytest.raises(RuntimeError, match="live_requires_explicit_user_confirmation"):
        binance_trading_worker.run(interval_seconds=60)


def test_live_worker_passes_policy_gate_only_after_explicit_user_confirmation(monkeypatch):
    monkeypatch.setattr(binance_trading_worker, "_read_control", lambda: _control("live", True))
    monkeypatch.setattr(
        binance_trading_worker.dbmod,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("Approved Live worker must proceed to preflight")),
    )
    with pytest.raises(AssertionError, match="proceed to preflight"):
        binance_trading_worker.run(interval_seconds=60)
