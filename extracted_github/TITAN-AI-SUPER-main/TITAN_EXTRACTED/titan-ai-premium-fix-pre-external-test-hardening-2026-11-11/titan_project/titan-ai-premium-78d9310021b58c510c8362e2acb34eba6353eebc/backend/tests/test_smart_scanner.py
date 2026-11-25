from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.data.binance_public import BinancePublicData
from app.engines.market_data import MarketDataEngine


def test_spot_scanner_filters_non_spot_and_low_liquidity_and_ranks(monkeypatch):
    monkeypatch.setattr(BinancePublicData, "get_spot_exchange_info", lambda self: {
        "symbols": [
            {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
             "status": "TRADING", "isSpotTradingAllowed": True},
            {"symbol": "ETHUSDT", "baseAsset": "ETH", "quoteAsset": "USDT",
             "status": "TRADING", "isSpotTradingAllowed": True},
            {"symbol": "XRPUSDT", "baseAsset": "XRP", "quoteAsset": "USDT",
             "status": "TRADING", "isSpotTradingAllowed": True},
            {"symbol": "BTCUPUSDT", "baseAsset": "BTCUP", "quoteAsset": "USDT",
             "status": "TRADING", "isSpotTradingAllowed": True},
            {"symbol": "BNBUSDT", "baseAsset": "BNB", "quoteAsset": "USDT",
             "status": "BREAK", "isSpotTradingAllowed": True},
            {"symbol": "SOLEUR", "baseAsset": "SOL", "quoteAsset": "EUR",
             "status": "TRADING", "isSpotTradingAllowed": True},
        ]
    })
    monkeypatch.setattr(BinancePublicData, "get_24h_tickers", lambda self: [
        {"symbol": "BTCUSDT", "quoteVolume": "90000000", "lastPrice": "60000",
         "priceChangePercent": "1.2", "volume": "1500", "count": 100000},
        {"symbol": "ETHUSDT", "quoteVolume": "25000000", "lastPrice": "3000",
         "priceChangePercent": "-0.5", "volume": "8000", "count": 80000},
        {"symbol": "XRPUSDT", "quoteVolume": "500000", "lastPrice": "0.5",
         "priceChangePercent": "2.0", "volume": "1000000", "count": 10000},
        {"symbol": "BTCUPUSDT", "quoteVolume": "3000000", "lastPrice": "1.0",
         "priceChangePercent": "5.0", "volume": "3000000", "count": 3000},
        {"symbol": "BNBUSDT", "quoteVolume": "10000000", "lastPrice": "500",
         "priceChangePercent": "0.1", "volume": "20000", "count": 10000},
    ])

    result = MarketDataEngine().scan_spot_universe(max_symbols=10, min_quote_volume_usdt=1_000_000)
    assert result["ok"] is True
    assert result["source"] == "binance_public_read_only"
    assert result["available_candidates"] == 2
    assert [item["symbol"] for item in result["items"]] == ["BTC/USDT", "ETH/USDT"]
    assert result["items"][0]["quote_volume_usdt_24h"] == 90_000_000.0
    assert "not a buy/sell signal" in result["note"]


def test_spot_scanner_caps_result_count_and_normalizes_bad_ticker_rows(monkeypatch):
    monkeypatch.setattr(BinancePublicData, "get_spot_exchange_info", lambda self: {
        "symbols": [{"symbol": "AAAUSDT", "baseAsset": "AAA", "quoteAsset": "USDT",
                     "status": "TRADING", "isSpotTradingAllowed": True}]
    })
    monkeypatch.setattr(BinancePublicData, "get_24h_tickers", lambda self: [
        {"symbol": "AAAUSDT", "quoteVolume": "bad", "lastPrice": "1"}
    ])
    result = MarketDataEngine().scan_spot_universe(max_symbols=500, min_quote_volume_usdt=0)
    assert result["returned"] == 0
    assert result["items"] == []


def test_scanner_http_endpoint_validates_limits_and_returns_read_only_data(monkeypatch):
    from app import star_app

    class RequestStub:
        query_params = {"limit": "101", "min_quote_volume_usdt": "0"}

    invalid = asyncio.run(star_app.scanner_spot(RequestStub()))
    assert invalid.status_code == 400

    captured = {}
    def fake_scan(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "items": [], "source": "binance_public_read_only"}

    monkeypatch.setattr(star_app.core_service.market_data, "scan_spot_universe", fake_scan)
    RequestStub.query_params = {"limit": "25", "min_quote_volume_usdt": "2500000"}
    response = asyncio.run(star_app.scanner_spot(RequestStub()))
    assert response.status_code == 200
    assert captured == {"max_symbols": 25, "min_quote_volume_usdt": 2_500_000.0}


def test_public_last_prices_accepts_only_recent_positive_marks(monkeypatch):
    monkeypatch.setattr("app.engines.market_data.time.time", lambda: 1000.0)
    monkeypatch.setattr(BinancePublicData, "get_24h_tickers", lambda self: [
        {"symbol": "BTCUSDT", "lastPrice": "60000", "closeTime": 1_000_000},
        {"symbol": "ETHUSDT", "lastPrice": "3000", "closeTime": 820_000},
        {"symbol": "SOLUSDT", "lastPrice": "0", "closeTime": 1_000_000},
        {"symbol": "XRPUSDT", "lastPrice": "1", "closeTime": 1_500_000},
    ])
    result = MarketDataEngine().get_public_last_prices({"BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"})
    assert result == {"BTC/USDT": 60000.0}


def test_futures_universe_scanner_only_returns_active_usdt_perpetuals(monkeypatch):
    from app.data.binance_futures_public import BinanceFuturesPublicData

    monkeypatch.setattr(BinanceFuturesPublicData, "get_exchange_info", lambda self: {
        "symbols": [
            {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
             "marginAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
            {"symbol": "ETHUSDT", "baseAsset": "ETH", "quoteAsset": "USDT",
             "marginAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
            {"symbol": "BTCUSD_PERP", "baseAsset": "BTC", "quoteAsset": "USD",
             "marginAsset": "BTC", "contractType": "PERPETUAL", "status": "TRADING"},
            {"symbol": "SOLUSDT", "baseAsset": "SOL", "quoteAsset": "USDT",
             "marginAsset": "USDT", "contractType": "CURRENT_QUARTER", "status": "TRADING"},
            {"symbol": "XRPUSDT", "baseAsset": "XRP", "quoteAsset": "USDT",
             "marginAsset": "USDT", "contractType": "PERPETUAL", "status": "PENDING_TRADING"},
        ]
    })
    monkeypatch.setattr(BinanceFuturesPublicData, "get_24h_tickers", lambda self: [
        {"symbol": "BTCUSDT", "quoteVolume": "90000000", "lastPrice": "60000",
         "priceChangePercent": "1.2", "volume": "1500", "count": 100000},
        {"symbol": "ETHUSDT", "quoteVolume": "25000000", "lastPrice": "3000",
         "priceChangePercent": "-0.5", "volume": "8000", "count": 80000},
        {"symbol": "SOLUSDT", "quoteVolume": "50000000", "lastPrice": "100",
         "priceChangePercent": "2", "volume": "1000", "count": 50000},
    ])
    result = MarketDataEngine().scan_futures_universe(
        max_symbols=10, min_quote_volume_usdt=1_000_000,
    )
    assert result["ok"] is True
    assert result["market"] == "futures"
    assert result["contract_type"] == "PERPETUAL"
    assert [item["symbol"] for item in result["items"]] == ["BTC/USDT", "ETH/USDT"]
    assert all(item["quote_asset"] == "USDT" for item in result["items"])


def test_futures_universe_scanner_rejects_invalid_limits():
    import pytest

    with pytest.raises(ValueError, match="max_symbols"):
        MarketDataEngine().scan_futures_universe(max_symbols=True)
    with pytest.raises(ValueError, match="min_quote_volume"):
        MarketDataEngine().scan_futures_universe(min_quote_volume_usdt=float("nan"))


def test_futures_scanner_requires_fresh_mark_price_and_funding(monkeypatch):
    import time
    from app.core.enums import Timeframe
    from app.services.smart_futures_scanner import SmartFuturesScannerService
    from app.services.core_service import core_service

    batches = core_service.market_data.get_multi_timeframe(
        "BTC/USDT", seed=12, market_type="futures",
    )
    for batch in batches.values():
        batch.source = SimpleNamespace(value="live_public")
        batch.market_type = "futures"
        batch.df.rows[-1]["close_time"] = int(time.time() * 1000)
    monkeypatch.setattr(core_service.market_data, "scan_futures_universe", lambda **kwargs: {
        "ok": True, "market": "futures", "available_candidates": 1,
        "items": [{"symbol": "BTC/USDT", "quote_volume_usdt_24h": 90_000_000}],
    })
    monkeypatch.setattr(
        core_service.market_data, "get_public_multi_timeframe",
        lambda symbol, market_type="spot": batches,
    )
    monkeypatch.setattr(core_service.market_data, "get_public_mark_price", lambda symbol: {
        "mark_price": 100.0, "last_funding_rate": 0.0001,
        "time": time.time() * 1000,
        "source": "binance_usdm_futures_public_read_only",
    })
    monkeypatch.setattr(core_service, "_validate_public_market_batches", lambda *a, **k: None)
    monkeypatch.setattr(core_service, "analyze", lambda *a, **k: {
        "action": "long", "confidence": 0.8, "decision_id": 123,
        "risk": {"ok": True}, "leverage": 2.0,
    })
    result = SmartFuturesScannerService().scan(object(), balance=150.0)
    assert result["ok"] is True
    assert result["market"] == "futures"
    assert result["items"][0]["action"] == "long"
    assert result["items"][0]["mark_price"] == 100.0
    assert result["items"][0]["funding_rate"] == 0.0001
    assert result["execution"] == "analysis_only_no_shadow_or_exchange_orders"


def test_futures_scanner_http_endpoint_validates_limits_and_uses_public_discovery(monkeypatch):
    from app import star_app

    class RequestStub:
        query_params = {"limit": "101", "min_quote_volume_usdt": "0"}

    invalid = asyncio.run(star_app.scanner_futures(RequestStub()))
    assert invalid.status_code == 400

    captured = {}
    def fake_scan(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "market": "futures", "items": [],
                "source": "binance_futures_public_read_only"}

    monkeypatch.setattr(star_app.core_service.market_data, "scan_futures_universe", fake_scan)
    RequestStub.query_params = {"limit": "20", "min_quote_volume_usdt": "12000000"}
    response = asyncio.run(star_app.scanner_futures(RequestStub()))
    assert response.status_code == 200
    assert captured == {"max_symbols": 20, "min_quote_volume_usdt": 12_000_000.0}


def test_futures_scanner_rejects_boolean_mark_price(monkeypatch):
    import time
    from app.services.smart_futures_scanner import SmartFuturesScannerService
    from app.services.core_service import core_service

    batches = core_service.market_data.get_multi_timeframe(
        "BTC/USDT", seed=13, market_type="futures",
    )
    for batch in batches.values():
        batch.source = SimpleNamespace(value="live_public")
        batch.market_type = "futures"
        batch.df.rows[-1]["close_time"] = int(time.time() * 1000)
    monkeypatch.setattr(core_service.market_data, "scan_futures_universe", lambda **kwargs: {
        "ok": True, "market": "futures", "available_candidates": 1,
        "items": [{"symbol": "BTC/USDT", "quote_volume_usdt_24h": 90_000_000}],
    })
    monkeypatch.setattr(core_service.market_data, "get_public_multi_timeframe",
        lambda symbol, market_type="spot": batches)
    monkeypatch.setattr(core_service.market_data, "get_public_mark_price", lambda symbol: {
        "mark_price": True, "last_funding_rate": 0.0001,
        "time": time.time() * 1000,
        "source": "binance_usdm_futures_public_read_only",
    })
    result = SmartFuturesScannerService().scan(object(), balance=150.0)
    assert result["ok"] is True
    assert result["analyzed"] == 0
    assert result["skipped"] == 1
    assert "invalid_boolean_or_missing_futures_mark_fields" in result["skipped_items"][0]["reason"]
