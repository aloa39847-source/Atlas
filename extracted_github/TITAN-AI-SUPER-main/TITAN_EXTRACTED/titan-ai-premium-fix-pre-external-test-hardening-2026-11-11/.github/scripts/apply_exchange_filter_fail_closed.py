from pathlib import Path
import sys

root = Path(sys.argv[1])
path = root / "app/integrations/binance_adapter.py"
s = path.read_text()

if "from math import isfinite" not in s:
    s = s.replace("import json", "import json\nfrom math import isfinite", 1)

anchor = '''    def place_order(self, symbol: str, side: str, order_type: str, quantity: float, price: float | None = None,
'''
helper = '''    def _load_order_filters(self, symbol: str, order_type: str) -> tuple[dict[str, float] | None, str | None]:
        """Fetch and normalize exchange filters; fail closed when symbol rules are unavailable."""
        info = self.get_exchange_info(symbol)
        if not isinstance(info, dict) or info.get("error") or not isinstance(info.get("symbols"), list):
            return None, "exchange_symbol_filters_unavailable"
        normalized = symbol.replace("/", "").upper()
        matches = [item for item in info["symbols"] if isinstance(item, dict)
                   and str(item.get("symbol", "")).upper() == normalized]
        if len(matches) != 1:
            return None, "exchange_symbol_filter_match_missing_or_ambiguous"
        item = matches[0]
        if str(item.get("status", "TRADING")).upper() != "TRADING":
            return None, "exchange_symbol_not_trading"
        raw_filters = item.get("filters")
        if not isinstance(raw_filters, list):
            return None, "exchange_symbol_filters_malformed"
        by_type = {str(f.get("filterType", "")).upper(): f for f in raw_filters if isinstance(f, dict)}
        price_filter = by_type.get("PRICE_FILTER")
        lot_filter = by_type.get("MARKET_LOT_SIZE") if order_type.upper() == "MARKET" else by_type.get("LOT_SIZE")
        if not isinstance(lot_filter, dict) or str(lot_filter.get("stepSize", "0")).strip() in {"", "0", "0.0"}:
            lot_filter = by_type.get("LOT_SIZE")
        if not isinstance(price_filter, dict) or not isinstance(lot_filter, dict):
            return None, "required_exchange_tick_or_step_filter_missing"
        min_values = []
        for kind in ("MIN_NOTIONAL", "NOTIONAL"):
            f = by_type.get(kind)
            if isinstance(f, dict):
                raw = f.get("minNotional", f.get("notional"))
                if raw is not None:
                    try:
                        value = float(raw)
                    except (TypeError, ValueError, OverflowError):
                        return None, "exchange_min_notional_filter_invalid"
                    if not isfinite(value) or value < 0:
                        return None, "exchange_min_notional_filter_invalid"
                    min_values.append(value)
        try:
            tick = float(price_filter.get("tickSize", 0) or 0)
            step = float(lot_filter.get("stepSize", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            return None, "exchange_tick_or_step_filter_invalid"
        if not isfinite(tick) or tick <= 0 or not isfinite(step) or step <= 0:
            return None, "exchange_tick_or_step_filter_invalid"
        if not min_values:
            return None, "exchange_min_notional_filter_missing"
        return {"tickSize": tick, "stepSize": step, "minNotional": max(min_values)}, None

'''
if helper.strip() not in s:
    if anchor not in s:
        raise SystemExit("Could not locate place_order insertion point")
    s = s.replace(anchor, helper + anchor, 1)

old = '''        pre = self.gateway.validate(
            symbol=symbol, market=MarketType(self.config.market),
            side=side, order_type=order_type, quantity=quantity, price=price,
            reference_price=reference_price, idempotency_key=idempotency_key,
            environment_mode="demo", live_enabled=False, bypass_attempt=False
        )
'''
new = '''        exchange_filters, filter_error = self._load_order_filters(symbol, order_type)
        if filter_error or not exchange_filters:
            return {"ok": False, "testnet": True, "sent_to_exchange": False,
                    "error": filter_error or "exchange_symbol_filters_unavailable"}
        pre = self.gateway.validate(
            symbol=symbol, market=MarketType(self.config.market),
            side=side, order_type=order_type, quantity=quantity, price=price,
            reference_price=reference_price, idempotency_key=idempotency_key,
            environment_mode="demo", live_enabled=False, bypass_attempt=False,
            exchange_filters=exchange_filters
        )
'''
if old in s:
    s = s.replace(old, new, 1)
elif "exchange_filters=exchange_filters" not in s:
    raise SystemExit("Could not locate standard order gateway call")

old_fut = '''                pre = self.gateway.validate(symbol=symbol, market=MarketType.FUTURES, side=expected_side,
                    order_type="market", quantity=quantity, reference_price=ref, idempotency_key=idempotency_key,
                    environment_mode="demo", live_enabled=False, bypass_attempt=False)
'''
new_fut = '''                exchange_filters, filter_error = self._load_order_filters(symbol, "MARKET")
                if filter_error or not exchange_filters:
                    return {"ok": False, "testnet": True, "sent_to_exchange": False,
                            "error": filter_error or "exchange_symbol_filters_unavailable"}
                pre = self.gateway.validate(symbol=symbol, market=MarketType.FUTURES, side=expected_side,
                    order_type="market", quantity=quantity, reference_price=ref, idempotency_key=idempotency_key,
                    environment_mode="demo", live_enabled=False, bypass_attempt=False,
                    exchange_filters=exchange_filters)
'''
if old_fut in s:
    s = s.replace(old_fut, new_fut, 1)
elif "exchange_filters=exchange_filters)" not in s:
    raise SystemExit("Could not locate futures close gateway call")

old_spot = '''                pre = self.gateway.validate(symbol=symbol, market=MarketType.SPOT, side="sell",
                    order_type="market", quantity=quantity, reference_price=ref, idempotency_key=idempotency_key,
                    environment_mode="demo", live_enabled=False, bypass_attempt=False)
'''
new_spot = '''                exchange_filters, filter_error = self._load_order_filters(symbol, "MARKET")
                if filter_error or not exchange_filters:
                    return {"ok": False, "testnet": True, "sent_to_exchange": False,
                            "error": filter_error or "exchange_symbol_filters_unavailable"}
                pre = self.gateway.validate(symbol=symbol, market=MarketType.SPOT, side="sell",
                    order_type="market", quantity=quantity, reference_price=ref, idempotency_key=idempotency_key,
                    environment_mode="demo", live_enabled=False, bypass_attempt=False,
                    exchange_filters=exchange_filters)
'''
if old_spot in s:
    s = s.replace(old_spot, new_spot, 1)
elif "exchange_filters=exchange_filters)" not in s:
    raise SystemExit("Could not locate spot close gateway call")

path.write_text(s)

test_file = root / "tests/test_testnet_idempotency.py"
if test_file.exists():
    t = test_file.read_text()
    helper = """
def _fake_symbol_info(symbol):
    return {"symbols": [{
        "symbol": symbol.replace("/", "").upper(), "status": "TRADING",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
        ],
    }]}


"""
    if "def _fake_symbol_info(symbol):" not in t:
        t = t.replace("def _adapter(monkeypatch):", helper + "def _adapter(monkeypatch):", 1)
    old_spot = """def _adapter(monkeypatch):
    monkeypatch.setenv("TITAN_TEST_API_KEY", "test-key")
    monkeypatch.setenv("TITAN_TEST_API_SECRET", "test-secret")
    return BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="testnet", market="spot",
        api_key_ref="env:TITAN_TEST_API_KEY",
        api_secret_ref="env:TITAN_TEST_API_SECRET",
    ))
"""
    new_spot = """def _adapter(monkeypatch):
    monkeypatch.setenv("TITAN_TEST_API_KEY", "test-key")
    monkeypatch.setenv("TITAN_TEST_API_SECRET", "test-secret")
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="testnet", market="spot",
        api_key_ref="env:TITAN_TEST_API_KEY",
        api_secret_ref="env:TITAN_TEST_API_SECRET",
    ))
    monkeypatch.setattr(adapter, "get_exchange_info", lambda symbol: _fake_symbol_info(symbol))
    return adapter
"""
    if old_spot in t:
        t = t.replace(old_spot, new_spot, 1)
    old_futures = """def _futures_close_adapter(monkeypatch):
    monkeypatch.setenv("TITAN_TEST_API_KEY", "test-key")
    monkeypatch.setenv("TITAN_TEST_API_SECRET", "test-secret")
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="testnet", market="futures",
        api_key_ref="env:TITAN_TEST_API_KEY", api_secret_ref="env:TITAN_TEST_API_SECRET",
    ))
    monkeypatch.setattr(adapter.public_data, "get_ticker_price", lambda symbol, market: 50000.0)
    return adapter
"""
    new_futures = """def _futures_close_adapter(monkeypatch):
    monkeypatch.setenv("TITAN_TEST_API_KEY", "test-key")
    monkeypatch.setenv("TITAN_TEST_API_SECRET", "test-secret")
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="testnet", market="futures",
        api_key_ref="env:TITAN_TEST_API_KEY", api_secret_ref="env:TITAN_TEST_API_SECRET",
    ))
    monkeypatch.setattr(adapter, "get_exchange_info", lambda symbol: _fake_symbol_info(symbol))
    monkeypatch.setattr(adapter.public_data, "get_ticker_price", lambda symbol, market: 50000.0)
    return adapter
"""
    if old_futures in t:
        t = t.replace(old_futures, new_futures, 1)
    test_file.write_text(t)

test = r'''from app.core.enums import MarketType
from app.integrations.binance_adapter import BinanceConfig, BinanceTestnetAdapter


def _exchange_info(filters=None, status="TRADING"):
    return {"symbols": [{
        "symbol": "BTCUSDT", "status": status,
        "filters": filters if filters is not None else [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
        ],
    }]}


def test_exchange_filter_loader_normalizes_spot_and_market_rules(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(market="spot"))
    monkeypatch.setattr(adapter, "get_exchange_info", lambda symbol: _exchange_info())
    filters, error = adapter._load_order_filters("BTC/USDT", "MARKET")
    assert error is None
    assert filters == {"tickSize": 0.1, "stepSize": 0.001, "minNotional": 5.0}


def test_futures_filter_loader_supports_binance_notional_field(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(market="futures"))
    info = _exchange_info([
        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
        {"filterType": "MIN_NOTIONAL", "notional": "10"},
    ])
    monkeypatch.setattr(adapter, "get_exchange_info", lambda symbol: info)
    filters, error = adapter._load_order_filters("BTCUSDT", "MARKET")
    assert error is None
    assert filters == {"tickSize": 0.01, "stepSize": 0.001, "minNotional": 10.0}


def test_exchange_filter_loader_fails_closed_on_missing_symbol_or_filters(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(market="futures"))
    monkeypatch.setattr(adapter, "get_exchange_info", lambda symbol: {"symbols": []})
    filters, error = adapter._load_order_filters("BTCUSDT", "MARKET")
    assert filters is None
    assert error == "exchange_symbol_filter_match_missing_or_ambiguous"


def test_exchange_filter_loader_rejects_malformed_numeric_filters(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(market="spot"))
    malformed = _exchange_info()
    malformed["symbols"][0]["filters"][0]["tickSize"] = "not-a-number"
    monkeypatch.setattr(adapter, "get_exchange_info", lambda symbol: malformed)
    filters, error = adapter._load_order_filters("BTCUSDT", "LIMIT")
    assert filters is None
    assert error == "exchange_tick_or_step_filter_invalid"


def test_exchange_filter_loader_rejects_non_trading_symbol(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(market="spot"))
    monkeypatch.setattr(adapter, "get_exchange_info", lambda symbol: _exchange_info(status="BREAK"))
    filters, error = adapter._load_order_filters("BTCUSDT", "LIMIT")
    assert filters is None
    assert error == "exchange_symbol_not_trading"


def test_testnet_place_order_never_sends_when_exchange_filters_unavailable(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="testnet", market="spot",
        api_key_ref="test-key", api_secret_ref="test-secret"))
    monkeypatch.setattr(adapter, "_api_key", lambda: "test-key")
    monkeypatch.setattr(adapter, "_api_secret", lambda: "test-secret")
    monkeypatch.setattr(adapter, "get_order_by_client_id",
                        lambda symbol, client_id: {"found": False, "confirmed_absent": True})
    monkeypatch.setattr(adapter, "_load_order_filters",
                        lambda symbol, order_type: (None, "exchange_symbol_filters_unavailable"))
    sent = []
    monkeypatch.setattr(adapter, "_request", lambda *a, **k: sent.append((a, k)) or {"orderId": 1})
    result = adapter.place_order("BTCUSDT", "BUY", "LIMIT", 0.001, price=50,
                                 reference_price=50, idempotency_key="titan-test-001")
    assert result["sent_to_exchange"] is False
    assert result["error"] == "exchange_symbol_filters_unavailable"
    assert sent == []
'''
(root / "tests/test_exchange_filter_fail_closed.py").write_text(test)
print("Testnet exchange-filter synchronization and fail-closed checks applied.")
