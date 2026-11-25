from pathlib import Path
import sys

root = Path(sys.argv[1])
path = root / "app/integrations/binance_adapter.py"
s = path.read_text()
old = '''            "withdrawal_permissions": False,
            "live_blocked": True,
'''
new = '''            # The adapter never exposes withdrawal operations, but it does not
            # inspect a user's Binance API-key restriction settings.
            "withdrawal_permissions": None,
            "withdrawal_permission_status": "unknown_not_checked",
            "withdrawal_operations_supported": False,
            "live_blocked": True,
'''
if old in s:
    s = s.replace(old, new, 1)
elif '"withdrawal_permission_status": "unknown_not_checked"' not in s:
    raise SystemExit("Could not locate withdrawal status fields")
path.write_text(s)

test_path = root / "tests/test_binance_status_validation.py"
t = test_path.read_text()
if "test_adapter_does_not_claim_api_key_withdrawals_are_disabled" not in t:
    t += '''

def test_adapter_does_not_claim_api_key_withdrawals_are_disabled(monkeypatch):
    adapter = _adapter(monkeypatch, response={"balances": [], "canTrade": True})
    result = adapter.status()
    assert result["withdrawal_permissions"] is None
    assert result["withdrawal_permission_status"] == "unknown_not_checked"
    assert result["withdrawal_operations_supported"] is False
'''
test_path.write_text(t)
print("Withdrawal capability and API-key permission status are now clearly distinguished.")
