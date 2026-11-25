from __future__ import annotations

from app.integrations.binance_adapter import BinanceConfig, BinanceTestnetAdapter


def test_order_fee_history_converts_base_and_third_asset_commissions_to_usdt(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="testnet", market="spot",
    ))
    trades = [
        {"commission": "0.001", "commissionAsset": "BTC", "price": "50000"},
        {"commission": "0.2", "commissionAsset": "USDT", "price": "50000"},
        {"commission": "0.01", "commissionAsset": "BNB", "price": "50000"},
    ]
    monkeypatch.setattr(adapter, "_request", lambda *args, **kwargs: trades)
    monkeypatch.setattr(adapter, "_market_price", lambda symbol: 600.0)

    result = adapter.get_order_fee_quote("BTCUSDT", 123)

    assert result["ok"] is True
    assert result["fees_paid_usdt"] == 56.2
    assert result["fee_asset"] == "USDT"
    assert result["quote_asset"] == "USDT"
    assert result["fee_breakdown"] == {"BTC": 0.001, "USDT": 0.2, "BNB": 0.01}


def test_order_fee_history_fails_closed_when_fee_asset_conversion_is_unavailable(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="testnet", market="spot",
    ))
    monkeypatch.setattr(adapter, "_request", lambda *args, **kwargs: [
        {"commission": "0.01", "commissionAsset": "BNB", "price": "50000"},
    ])

    def unavailable(_symbol):
        raise RuntimeError("conversion market unavailable")

    monkeypatch.setattr(adapter, "_market_price", unavailable)
    result = adapter.get_order_fee_quote("BTCUSDT", 123)

    assert result["ok"] is False
    assert result["pending"] is True


def test_non_usdt_quote_commissions_are_converted_to_usdt(monkeypatch):
    adapter = BinanceTestnetAdapter(BinanceConfig(
        enabled=True, environment="testnet", market="spot",
    ))
    monkeypatch.setattr(adapter, "_request", lambda *args, **kwargs: [
        {"commission": "0.00001", "commissionAsset": "BTC", "price": "0.05"},
    ])
    seen = []
    def market_price(symbol):
        seen.append(symbol)
        return 50_000.0

    monkeypatch.setattr(adapter, "_market_price", market_price)
    result = adapter.get_order_fee_quote("BNBBTC", 456)

    assert result["ok"] is True
    assert result["fees_paid_usdt"] == 0.5
    assert result["quote_asset"] == "BTC"
    assert seen == ["BTCUSDT"]
