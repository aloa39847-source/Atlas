from pathlib import Path
import sys

root = Path(sys.argv[1])
path = root / "app/integrations/binance_adapter.py"
s = path.read_text()

old = '''                result["connected"] = "error" not in acct
                result["account_check"] = "ok" if "error" not in acct else "error"
'''
new = '''                payload_valid = isinstance(acct, dict) and not acct.get("error")
                if payload_valid and "code" in acct:
                    try:
                        payload_valid = int(acct.get("code")) >= 0
                    except (TypeError, ValueError, OverflowError):
                        payload_valid = False
                if payload_valid and self.config.market == "futures":
                    payload_valid = isinstance(acct.get("assets"), list) and isinstance(acct.get("positions"), list)
                elif payload_valid and self.config.market == "spot":
                    payload_valid = isinstance(acct.get("balances"), list)
                else:
                    payload_valid = False
                result["connected"] = bool(payload_valid)
                result["account_check"] = "ok" if payload_valid else "invalid_or_error_account_payload"
                result["trading_allowed"] = acct.get("canTrade") if payload_valid and isinstance(acct.get("canTrade"), bool) else None
'''
if old in s:
    s = s.replace(old, new, 1)
elif "invalid_or_error_account_payload" not in s:
    raise SystemExit("Could not locate account status validation block")
path.write_text(s)

test_path = root / "tests/test_binance_status_validation.py"
test = r'''from app.integrations.binance_adapter import BinanceConfig, BinanceTestnetAdapter


def _adapter(monkeypatch, market="spot", response=None):
    monkeypatch.setenv("TITAN_STATUS_KEY", "test-key")
    monkeypatch.setenv("TITAN_STATUS_SECRET", "test-secret")
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="testnet", market=market,
        api_key_ref="env:TITAN_STATUS_KEY", api_secret_ref="env:TITAN_STATUS_SECRET"))
    monkeypatch.setattr(adapter, "_request", lambda *a, **k: response)
    return adapter


def test_spot_status_requires_expected_account_shape(monkeypatch):
    adapter = _adapter(monkeypatch, response={"balances": [], "canTrade": True})
    result = adapter.status()
    assert result["connected"] is True
    assert result["account_check"] == "ok"
    assert result["trading_allowed"] is True


def test_negative_api_code_is_not_reported_as_connected(monkeypatch):
    adapter = _adapter(monkeypatch, response={"code": -2015, "msg": "Invalid API-key"})
    result = adapter.status()
    assert result["connected"] is False
    assert result["account_check"] == "invalid_or_error_account_payload"
    assert result["trading_allowed"] is None


def test_futures_status_requires_assets_and_positions(monkeypatch):
    adapter = _adapter(monkeypatch, market="futures", response={"positions": [], "canTrade": True})
    result = adapter.status()
    assert result["connected"] is False
    assert result["account_check"] == "invalid_or_error_account_payload"


def test_connected_account_can_be_read_only_without_trade_permission(monkeypatch):
    adapter = _adapter(monkeypatch, response={"balances": [], "canTrade": False})
    result = adapter.status()
    assert result["connected"] is True
    assert result["trading_allowed"] is False
'''
test_path.write_text(test)

print("Binance account status now validates response shape and trading permission separately.")
