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


def test_live_worker_is_hard_disabled_before_any_database_or_exchange_access(monkeypatch):
    for confirmed in (False, True):
        monkeypatch.setattr(binance_trading_worker, "_read_control", lambda confirmed=confirmed: _control("live", confirmed))
        monkeypatch.setattr(
            binance_trading_worker.dbmod,
            "SessionLocal",
            lambda: (_ for _ in ()).throw(AssertionError("Live worker must not open a database session")),
        )
        with pytest.raises(RuntimeError, match="live_trading_hard_disabled_in_this_release"):
            binance_trading_worker.run(interval_seconds=60)



def test_retired_testnet_patch_helpers_cannot_reenable_direct_order_or_close_routes():
    import ast
    from pathlib import Path

    scripts = (
        "apply_testnet_order_reconciliation.py",
        "apply_testnet_idempotency.py",
        "apply_safe_testnet_close.py",
    )
    script_dir = Path(__file__).resolve().parents[4] / ".github" / "scripts"
    project_root = Path(__file__).resolve().parents[2]
    # Outer repository-only migration helpers are not shipped in the
    # standalone project ZIP; validate them when present, but always validate
    # the in-project legacy UI patch helper.
    targets = [script_dir / name for name in scripts if (script_dir / name).is_file()]
    targets.append(project_root / "_patch_ui.py")
    env_example = (project_root / ".env.example").read_text(encoding="utf-8")
    for credential_name in (
        "BINANCE_API_KEY", "BINANCE_API_SECRET",
        "BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET",
        "BINANCE_TESTNET_FUTURES_API_KEY", "BINANCE_TESTNET_FUTURES_API_SECRET",
    ):
        assert f"{credential_name}=" not in env_example
    assert "authenticated TITAN dashboard" in env_example
    for path in targets:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert "DISABLED:" in source
        assert any(isinstance(node, ast.Raise) for node in ast.walk(tree))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {
                    "write_text", "write_bytes", "place_order", "close_position",
                }
