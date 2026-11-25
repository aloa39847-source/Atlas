from datetime import datetime, timezone, timedelta

from app.engines.arbitrage import ArbitrageAnalysisEngine


def _quotes(now):
    stamp = now.isoformat()
    return {
        "BTC/USDT": {
            "symbol": "BTC/USDT", "source": "binance_public_read_only",
            "best_bid": 50000.0, "best_ask": 50010.0, "fetched_at": stamp,
        },
        "ETH/BTC": {
            "symbol": "ETH/BTC", "source": "binance_public_read_only",
            "best_bid": 0.05, "best_ask": 0.0501, "fetched_at": stamp,
        },
        "ETH/USDT": {
            "symbol": "ETH/USDT", "source": "binance_public_read_only",
            "best_bid": 2600.0, "best_ask": 2601.0, "fetched_at": stamp,
        },
    }


def test_triangular_spot_arbitrage_calculates_three_fee_adjusted_legs():
    now = datetime.now(timezone.utc)
    result = ArbitrageAnalysisEngine().analyze_triangle(
        ["USDT", "BTC", "ETH", "USDT"], _quotes(now), now=now,
    )
    assert result["ok"] is True
    assert result["candidate"] is True
    assert result["profit_percent_estimate"] > 0.05
    assert len(result["legs"]) == 3
    assert result["execution"] == "none"
    assert result["source"] == "binance_public_read_only"


def test_triangular_spot_arbitrage_respects_costs_and_threshold():
    now = datetime.now(timezone.utc)
    result = ArbitrageAnalysisEngine().analyze_triangle(
        ["USDT", "BTC", "ETH", "USDT"], _quotes(now),
        fee_rate=0.01, slippage_rate=0.01, min_profit_pct=0.05, now=now,
    )
    assert result["ok"] is True
    assert result["candidate"] is False
    assert result["assessment"] == "no_edge_above_threshold"


def test_triangular_arbitrage_rejects_invalid_route_or_untrusted_quotes():
    now = datetime.now(timezone.utc)
    engine = ArbitrageAnalysisEngine()
    invalid_route = engine.analyze_triangle(
        ["USDT", "BTC", "ETH", "BTC"], _quotes(now), now=now,
    )
    assert invalid_route["ok"] is False

    quotes = _quotes(now)
    quotes["BTC/USDT"]["source"] = "synthetic"
    untrusted = engine.analyze_triangle(
        ["USDT", "BTC", "ETH", "USDT"], quotes, now=now,
    )
    assert untrusted["ok"] is False


def test_triangular_arbitrage_rejects_stale_or_inconsistent_quotes():
    now = datetime.now(timezone.utc)
    stale = _quotes(now)
    stale["ETH/BTC"]["fetched_at"] = (now - timedelta(seconds=60)).isoformat()
    result = ArbitrageAnalysisEngine().analyze_triangle(
        ["USDT", "BTC", "ETH", "USDT"], stale, now=now,
    )
    assert result["ok"] is False
    assert result["error"] == "stale_or_future_quote"

    crossed = _quotes(now)
    crossed["BTC/USDT"]["best_bid"] = crossed["BTC/USDT"]["best_ask"]
    result = ArbitrageAnalysisEngine().analyze_triangle(
        ["USDT", "BTC", "ETH", "USDT"], crossed, now=now,
    )
    assert result["ok"] is False
    assert result["error"] == "invalid_or_crossed_quote"


def test_triangular_arbitrage_rejects_boolean_and_non_finite_configuration():
    now = datetime.now(timezone.utc)
    engine = ArbitrageAnalysisEngine()
    for kwargs in ({"fee_rate": True}, {"starting_amount": float("inf")}):
        result = engine.analyze_triangle(
            ["USDT", "BTC", "ETH", "USDT"], _quotes(now), now=now, **kwargs,
        )
        assert result["ok"] is False

def test_triangular_arbitrage_rejects_duplicate_normalized_quote_symbols():
    now = datetime.now(timezone.utc)
    quotes = _quotes(now)
    quotes["BTCUSDT"] = dict(quotes["BTC/USDT"])
    result = ArbitrageAnalysisEngine().analyze_triangle(
        ["USDT", "BTC", "ETH", "USDT"], quotes, now=now,
    )
    assert result["ok"] is False
    assert result["error"] == "duplicate_normalized_market_quote"
    assert result["candidate"] is False
    assert result["execution"] == "none"


def test_triangular_arbitrage_rejects_malformed_market_symbol_key():
    now = datetime.now(timezone.utc)
    quotes = _quotes(now)
    quotes["BTC-USDT"] = dict(quotes.pop("BTC/USDT"))
    result = ArbitrageAnalysisEngine().analyze_triangle(
        ["USDT", "BTC", "ETH", "USDT"], quotes, now=now,
    )
    assert result["ok"] is False
    assert result["error"] == "verified_binance_spot_public_quotes_required"
