from pathlib import Path
import sys

root = Path(sys.argv[1])

# Add a read-only ticker-price lookup to the public adapter.
p = root / "app/data/binance_public.py"
s = p.read_text()
if "def get_ticker_price(" not in s:
    marker = "    def klines_to_candles(self, klines: list[list[Any]]) -> list[dict]:\n"
    method = '''    def get_ticker_price(self, symbol: str, market: str = "spot") -> float:
        market = (market or "spot").lower()
        if market not in self.ALLOWED_MARKETS:
            raise ValueError("unsupported_market_for_public_ticker")
        base = self.FUTURES_BASE if market == "futures" else self.SPOT_BASE
        path = "/fapi/v1/ticker/price" if market == "futures" else "/api/v3/ticker/price"
        query = urllib.parse.urlencode({"symbol": symbol.replace("/", "").upper()})
        url = f"{base}{path}?{query}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TITAN-AI-PREMIUM/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            price = float(data["price"])
            if price <= 0:
                raise ValueError("invalid_public_ticker_price")
            return price
        except Exception as e:
            raise RuntimeError(f"binance_public_ticker_error:{e}") from e

'''
    if marker not in s:
        raise SystemExit("Binance public ticker insertion marker missing")
    s = s.replace(marker, method + marker, 1)
    p.write_text(s)

# Market-order notional must use a server-fetched reference price, never the caller's value.
p = root / "app/integrations/binance_adapter.py"
s = p.read_text()
if "from app.data.binance_public import BinancePublicData" not in s:
    s = s.replace(
        "from app.engines.order_gateway import PreTradeOrderGateway",
        "from app.engines.order_gateway import PreTradeOrderGateway\nfrom app.data.binance_public import BinancePublicData",
        1,
    )
if "self.public_data = BinancePublicData()" not in s:
    s = s.replace(
        "        self.gateway = PreTradeOrderGateway()\n",
        "        self.gateway = PreTradeOrderGateway()\n        self.public_data = BinancePublicData()\n",
        1,
    )
if "market_reference_price_unavailable" not in s:
    marker = '''        pre = self.gateway.validate(
            symbol=symbol, market=MarketType(self.config.market),
'''
    guard = '''        if order_type.upper() == "MARKET":
            try:
                # Never trust a caller-supplied reference price for market-order sizing.
                reference_price = self.public_data.get_ticker_price(symbol, market=self.config.market) * 1.01
            except Exception as e:
                return {"ok": False, "testnet": True, "sent_to_exchange": False,
                        "error": "market_reference_price_unavailable", "details": str(e)}
'''
    if marker not in s:
        raise SystemExit("Market-order price guard insertion marker missing")
    s = s.replace(marker, guard + marker, 1)
    p.write_text(s)

test_path = root / "tests/test_testnet_idempotency.py"
if not test_path.exists():
    raise SystemExit("Idempotency test file missing")
test_source = test_path.read_text()
if "test_market_order_uses_server_fetched_price_not_caller_price" not in test_source:
    test_source += r'''

def test_market_order_uses_server_fetched_price_not_caller_price(monkeypatch):
    adapter = _adapter(monkeypatch)
    calls = []
    monkeypatch.setattr(adapter.public_data, "get_ticker_price", lambda symbol, market: 50000.0)

    def fake_request(method, path, signed=False, params=None):
        calls.append(method)
        if method == "GET":
            return {"error": "http_error", "status": 400, "body": '{"code":-2013,"msg":"Order does not exist."}'}
        return {"orderId": 101}

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.place_order("BTC/USDT", "BUY", "MARKET", 0.002,
                                 reference_price=1.0, idempotency_key="titan-order-0004")
    assert result["error"] == "pretrade_blocked"
    assert calls == ["GET"]


def test_market_order_fails_closed_if_server_price_is_unavailable(monkeypatch):
    adapter = _adapter(monkeypatch)
    calls = []

    def fail_price(symbol, market):
        raise RuntimeError("public feed offline")

    monkeypatch.setattr(adapter.public_data, "get_ticker_price", fail_price)

    def fake_request(method, path, signed=False, params=None):
        calls.append(method)
        return {"error": "http_error", "status": 400, "body": '{"code":-2013,"msg":"Order does not exist."}'}

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.place_order("BTC/USDT", "BUY", "MARKET", 0.001,
                                 reference_price=1.0, idempotency_key="titan-order-0005")
    assert result["error"] == "market_reference_price_unavailable"
    assert calls == ["GET"]
'''
    test_path.write_text(test_source)
