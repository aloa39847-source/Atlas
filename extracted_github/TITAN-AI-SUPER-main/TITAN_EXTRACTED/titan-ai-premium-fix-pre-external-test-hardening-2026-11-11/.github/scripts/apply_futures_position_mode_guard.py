from pathlib import Path
import sys

root = Path(sys.argv[1])
path = root / "app/integrations/binance_adapter.py"
s = path.read_text()

method = '''    def get_position_mode(self) -> dict[str, Any]:
        """Read Futures account position mode; unknown mode blocks new Futures orders."""
        if self.config.market != "futures":
            return {"dualSidePosition": False}
        result = self._request("GET", "/fapi/v1/positionSide/dual", signed=True)
        if not isinstance(result, dict) or result.get("error") or not isinstance(result.get("dualSidePosition"), bool):
            return {"error": "futures_position_mode_unavailable", "details": result}
        return result

'''
if "def get_position_mode(self)" not in s:
    marker = "    def get_account(self) -> dict[str, Any]:"
    if marker not in s:
        raise SystemExit("Could not locate account methods for position-mode helper")
    s = s.replace(marker, method + marker, 1)

old = '''        if order_type.upper() == "MARKET":
            try:
'''
new = '''        exchange_side = str(side or "").upper()
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

        if order_type.upper() == "MARKET":
            try:
'''
if old in s and "hedge_mode_requires_explicit_long_or_short" not in s:
    s = s.replace(old, new, 1)
elif "hedge_mode_requires_explicit_long_or_short" not in s:
    raise SystemExit("Could not locate order side normalization insertion")

s=s.replace('"side": side.upper(),', '"side": exchange_side,', 1)
marker = '''            "newClientOrderId": idempotency_key,
        }
        if price and order_type.upper() in ("LIMIT",):
'''
replacement = '''            "newClientOrderId": idempotency_key,
        }
        if position_side:
            params["positionSide"] = position_side
        if price and order_type.upper() in ("LIMIT",):
'''
if marker in s:
    s = s.replace(marker, replacement, 1)
elif 'params["positionSide"] = position_side' not in s:
    raise SystemExit("Could not locate order parameter construction")

path.write_text(s)

test_path = root / "tests/test_futures_position_mode.py"
test = r'''from app.integrations.binance_adapter import BinanceConfig, BinanceTestnetAdapter


def _adapter(monkeypatch, dual_side):
    monkeypatch.setenv("TITAN_FUTURES_KEY", "test-key")
    monkeypatch.setenv("TITAN_FUTURES_SECRET", "test-secret")
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="testnet", market="futures",
        api_key_ref="env:TITAN_FUTURES_KEY", api_secret_ref="env:TITAN_FUTURES_SECRET"))
    monkeypatch.setattr(adapter, "get_order_by_client_id",
                        lambda symbol, client_id: {"found": False, "confirmed_absent": True})
    monkeypatch.setattr(adapter, "get_exchange_info", lambda symbol: {"symbols": [{
        "symbol": symbol.replace("/", "").upper(), "status": "TRADING", "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ]}]})
    monkeypatch.setattr(adapter, "get_position_mode", lambda: {"dualSidePosition": dual_side})
    monkeypatch.setattr(adapter.public_data, "get_ticker_price", lambda symbol, market: 50000.0)
    return adapter


def test_futures_one_way_mode_maps_long_short_to_exchange_buy_sell(monkeypatch):
    adapter = _adapter(monkeypatch, False)
    posts = []
    monkeypatch.setattr(adapter, "_request", lambda method, path, signed=False, params=None:
                        posts.append(params) or {"orderId": 10} if method == "POST" else {"ok": True})
    result = adapter.place_order("BTCUSDT", "LONG", "LIMIT", 0.001, price=50000,
                                 idempotency_key="futures-oneway-001")
    assert result["ok"] is True
    assert posts[0]["side"] == "BUY"
    assert "positionSide" not in posts[0]


def test_futures_hedge_mode_sets_exchange_side_and_position_side(monkeypatch):
    adapter = _adapter(monkeypatch, True)
    posts = []
    monkeypatch.setattr(adapter, "_request", lambda method, path, signed=False, params=None:
                        posts.append(params) or {"orderId": 11} if method == "POST" else {"ok": True})
    result = adapter.place_order("BTCUSDT", "SHORT", "LIMIT", 0.001, price=50000,
                                 idempotency_key="futures-hedge-001")
    assert result["ok"] is True
    assert posts[0]["side"] == "SELL"
    assert posts[0]["positionSide"] == "SHORT"


def test_futures_hedge_mode_rejects_ambiguous_buy_sell_intent(monkeypatch):
    adapter = _adapter(monkeypatch, True)
    sent = []
    monkeypatch.setattr(adapter, "_request", lambda *a, **k: sent.append((a, k)) or {"orderId": 12})
    result = adapter.place_order("BTCUSDT", "BUY", "LIMIT", 0.001, price=50000,
                                 idempotency_key="futures-hedge-002")
    assert result["sent_to_exchange"] is False
    assert result["error"] == "hedge_mode_requires_explicit_long_or_short"
    assert sent == []


def test_futures_order_blocks_when_position_mode_cannot_be_verified(monkeypatch):
    adapter = _adapter(monkeypatch, False)
    monkeypatch.setattr(adapter, "get_position_mode", lambda: {"error": "futures_position_mode_unavailable"})
    sent = []
    monkeypatch.setattr(adapter, "_request", lambda *a, **k: sent.append((a, k)) or {"orderId": 13})
    result = adapter.place_order("BTCUSDT", "BUY", "LIMIT", 0.001, price=50000,
                                 idempotency_key="futures-mode-unknown")
    assert result["sent_to_exchange"] is False
    assert result["error"] == "futures_position_mode_unavailable"
    assert sent == []
'''
test_path.write_text(test)

print("Futures position-mode preflight and unambiguous side mapping applied.")
