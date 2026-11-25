#!/usr/bin/env python3
"""Remove duplicated Futures position-mode normalization and add regression coverage."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
adapter_path = ROOT / "app/integrations/binance_adapter.py"
source = adapter_path.read_text()

block = '''        exchange_side = str(side or "").upper()
        position_side = None
        if self.config.market == "futures":
            position_mode = self.get_position_mode()
            if position_mode.get("error"):
                return {"ok": False, "testnet": True, "sent_to_exchange": False,
                        "error": "futures_position_mode_unavailable", "details": position_mode}
            if position_mode["dualSidePosition"]:
                if exchange_side not in {"LONG", "SHORT"}:
                    return {"ok": False, "testnet": True, "sent_to_exchange": False,
                            "error": "hedge_mode_requires_explicit_long_or_short"}
                position_side = exchange_side
                exchange_side = "BUY" if position_side == "LONG" else "SELL"
            elif exchange_side == "LONG":
                exchange_side = "BUY"
            elif exchange_side == "SHORT":
                exchange_side = "SELL"
            elif exchange_side not in {"BUY", "SELL"}:
                return {"ok": False, "testnet": True, "sent_to_exchange": False,
                        "error": "invalid_futures_order_side"}
        elif exchange_side not in {"BUY", "SELL"}:
            return {"ok": False, "testnet": True, "sent_to_exchange": False,
                    "error": "invalid_spot_order_side"}

'''
count = source.count(block)
if count in {3, 4}:
    source = source.replace(block * count, block, 1)
elif count != 1:
    raise SystemExit(f"Unexpected Futures side-normalization block count: {count}")
adapter_path.write_text(source)

test_path = ROOT / "tests/test_futures_order_side_normalization.py"
test_source = '''from types import SimpleNamespace

import pytest

from app.integrations.binance_adapter import BinanceConfig, BinanceTestnetAdapter


@pytest.mark.parametrize(
    ("dual_side", "requested_side", "expected_side", "expected_position_side"),
    [
        (False, "LONG", "BUY", None),
        (False, "SHORT", "SELL", None),
        (True, "LONG", "BUY", "LONG"),
        (True, "SHORT", "SELL", "SHORT"),
    ],
)
def test_futures_side_normalization_reads_position_mode_once(
    monkeypatch, dual_side, requested_side, expected_side, expected_position_side
):
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="testnet", market="futures",
        api_key_ref="env:BINANCE_TESTNET_API_KEY",
        api_secret_ref="env:BINANCE_TESTNET_API_SECRET",
    ))
    monkeypatch.setattr(adapter, "_api_key", lambda: "test-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "test-secret")
    monkeypatch.setattr(adapter, "get_order_by_client_id",
                        lambda *_: {"found": False, "confirmed_absent": True})
    calls = {"position_mode": 0, "request": []}

    def position_mode():
        calls["position_mode"] += 1
        return {"dualSidePosition": dual_side}

    monkeypatch.setattr(adapter, "get_position_mode", position_mode)
    monkeypatch.setattr(adapter.public_data, "get_ticker_price", lambda *_args, **_kwargs: 100.0)
    monkeypatch.setattr(adapter, "_load_order_filters",
                        lambda *_: ({"tickSize": 0.01, "stepSize": 0.001, "minNotional": 5.0}, None))
    monkeypatch.setattr(adapter.gateway, "validate",
                        lambda **_kwargs: SimpleNamespace(ok=True, to_dict=lambda: {"ok": True}))
    def request(method, path, signed=False, params=None):
        calls["request"].append((method, path, params or {}))
        return {"orderId": 123, "status": "NEW"}
    monkeypatch.setattr(adapter, "_request", request)

    result = adapter.place_order(
        "BTCUSDT", requested_side, "MARKET", 0.1,
        reference_price=100.0, idempotency_key="titan-futures-01",
    )

    assert result["ok"] is True
    assert calls["position_mode"] == 1
    assert len(calls["request"]) == 1
    params = calls["request"][0][2]
    assert params["side"] == expected_side
    if expected_position_side:
        assert params["positionSide"] == expected_position_side
    else:
        assert "positionSide" not in params
'''
if test_path.exists():
    if test_path.read_text() != test_source:
        raise SystemExit("Existing regression test differs; refusing to overwrite")
else:
    test_path.write_text(test_source)
print("Applied single-read Futures position-mode normalization and regression tests.")
