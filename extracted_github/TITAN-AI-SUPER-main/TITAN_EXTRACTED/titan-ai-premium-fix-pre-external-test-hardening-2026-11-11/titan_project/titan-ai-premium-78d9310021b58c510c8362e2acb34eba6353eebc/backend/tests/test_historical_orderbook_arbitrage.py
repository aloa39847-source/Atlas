import asyncio
import json
from datetime import datetime, timezone

from app import star_app
from app.engines.specialized_backtest import SpecializedBacktestEngine


ROUTE = ["USDT", "BTC", "ETH", "USDT"]


def _quotes(depth=1000):
    return {
        "BTCUSDT": {
            "symbol": "BTCUSDT", "source": "binance_public_read_only",
            "bids": [[9.9, depth]], "asks": [[10.0, depth]],
        },
        "ETHBTC": {
            "symbol": "ETHBTC", "source": "binance_public_read_only",
            "bids": [[0.049, depth]], "asks": [[0.05, depth]],
        },
        "ETHUSDT": {
            "symbol": "ETHUSDT", "source": "binance_public_read_only",
            "bids": [[0.6, depth]], "asks": [[0.61, depth]],
        },
    }


def test_historical_orderbook_triangle_walks_all_three_books_and_charges_fees():
    result = SpecializedBacktestEngine(
        initial_cash=100, fee_rate=0, slippage_rate=0,
    ).run_triangular_orderbook_replay(
        [{"timestamp": 1000, "quotes": _quotes()}],
        route=ROUTE, min_profit_pct=5,
    )
    assert result["ok"] is True
    assert result["total_trades"] == 1
    assert result["cycles"][0]["ending_amount"] == 120
    assert result["cycles"][0]["legs"][0]["execution_side"] == "buy_base_from_asks"
    assert result["cycles"][0]["legs"][0]["levels_consumed"][0]["quote_value"] == 100
    assert result["no_lookahead"] is True
    assert result["no_live_orders"] is True


def test_historical_orderbook_triangle_rejects_insufficient_depth_without_inventing_fill():
    result = SpecializedBacktestEngine(
        initial_cash=100, fee_rate=0, slippage_rate=0,
    ).run_triangular_orderbook_replay(
        [{"timestamp": 1000, "quotes": _quotes(depth=5)}],
        route=ROUTE, min_profit_pct=0,
    )
    assert result["ok"] is True
    assert result["total_trades"] == 0
    assert result["rejected_insufficient_depth"] == 1
    assert result["rejected"][0]["reason"] == "insufficient_recorded_depth"


def test_historical_orderbook_triangle_rejects_unsorted_or_unverified_depth():
    quotes = _quotes()
    quotes["BTCUSDT"]["asks"] = [[10.1, 1], [10.0, 2]]
    result = SpecializedBacktestEngine().run_triangular_orderbook_replay(
        [{"timestamp": 1000, "quotes": quotes}], route=ROUTE,
    )
    assert result["ok"] is False
    assert "not_price_sorted" in result["error"]

    quotes = _quotes()
    quotes["ETHBTC"]["source"] = "synthetic"
    result = SpecializedBacktestEngine().run_triangular_orderbook_replay(
        [{"timestamp": 1000, "quotes": quotes}], route=ROUTE,
    )
    assert result["ok"] is False
    assert result["error"] == "verified_binance_spot_orderbook_required"


def test_orderbook_capture_persists_only_synchronized_public_depth(monkeypatch):
    class FakeDB:
        def __init__(self):
            self.row = None
        def add(self, row):
            row.id = 17
            self.row = row
        def commit(self): pass
        def refresh(self, row): pass
        def rollback(self): pass
        def close(self): pass

    db = FakeDB()
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app, "require_user", lambda request, session: (None, None))
    stamp = datetime.now(timezone.utc).isoformat()
    quotes = _quotes()
    for symbol, quote in quotes.items():
        quote["fetched_at"] = stamp
    def fake_book(symbol, limit, market_type):
        if symbol not in quotes:
            raise RuntimeError("symbol_not_found")
        return dict(quotes[symbol])
    monkeypatch.setattr(star_app.core_service.market_data, "get_public_order_book", fake_book)

    async def request_body(_request):
        return {"route": ROUTE, "depth_limit": 100}
    monkeypatch.setattr(star_app, "body", request_body)
    response = asyncio.run(star_app.capture_triangle_orderbook(None))
    result = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert result["persisted"] is True
    assert result["snapshot_id"] == 17
    assert set(db.row.quotes) == set(quotes)
    assert db.row.source == "binance_public_read_only"
