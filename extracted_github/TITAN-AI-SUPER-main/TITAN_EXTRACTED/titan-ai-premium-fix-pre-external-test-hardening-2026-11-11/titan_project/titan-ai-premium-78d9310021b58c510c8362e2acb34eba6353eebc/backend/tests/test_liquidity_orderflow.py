from app.data.binance_public import BinancePublicData
from app.engines.liquidity import LiquidityAnalysisEngine
from app.engines.market_data import MarketDataEngine
from app.engines.order_flow import OrderFlowEngine
import pytest


def test_public_order_book_metrics_and_liquidity_analysis(monkeypatch):
    monkeypatch.setattr(BinancePublicData, "get_order_book", lambda self, symbol, limit: {
        "lastUpdateId": 123,
        "bids": [["99", "2"], ["98", "3"]],
        "asks": [["101", "1"], ["102", "4"]],
    })
    snapshot = MarketDataEngine().get_public_order_book("BTC/USDT", 20)
    assert snapshot["best_bid"] == 99.0
    assert snapshot["best_ask"] == 101.0
    assert snapshot["spread_percent"] > 0
    assert -1.0 <= snapshot["depth_imbalance"] <= 1.0
    analysis = LiquidityAnalysisEngine().analyze_order_book(snapshot)
    assert analysis["ok"] is True
    assert analysis["signal"] == "not_generated"
    assert analysis["execution"] == "none"


def test_public_order_book_rejects_crossed_or_empty_depth(monkeypatch):
    monkeypatch.setattr(BinancePublicData, "get_order_book", lambda self, symbol, limit: {
        "bids": [["101", "1"]], "asks": [["100", "1"]],
    })
    with pytest.raises(RuntimeError, match="crossed_or_invalid"):
        MarketDataEngine().get_public_order_book("BTC/USDT", 20)


def test_public_aggregate_trades_calculate_taker_side_correctly(monkeypatch):
    import time
    now_ms = int(time.time() * 1000)
    monkeypatch.setattr("app.engines.market_data.time.time", lambda: now_ms / 1000.0)
    monkeypatch.setattr(BinancePublicData, "get_aggregate_trades", lambda self, symbol, limit: [
        {"p": "100", "q": "2", "T": now_ms - 2_000, "m": False},
        {"p": "101", "q": "1", "T": now_ms - 1_000, "m": True},
        {"p": "102", "q": "3", "T": now_ms, "m": False},
    ])
    snapshot = MarketDataEngine().get_public_order_flow("BTC/USDT", 3)
    assert snapshot["buy_taker_count"] == 2
    assert snapshot["sell_taker_count"] == 1
    assert snapshot["buy_taker_quote_volume"] == 506.0
    assert snapshot["sell_taker_quote_volume"] == 101.0
    analysis = OrderFlowEngine().analyze_trades(snapshot)
    assert analysis["ok"] is True
    assert analysis["taker_flow_delta_quote"] == 405.0
    assert analysis["signal"] == "not_generated"


def test_liquidity_and_order_flow_engines_reject_untrusted_or_invalid_data():
    liquidity = LiquidityAnalysisEngine().analyze_order_book({"source": "synthetic"})
    assert liquidity["ok"] is False
    flow = OrderFlowEngine().analyze_trades({
        "source": "binance_public_read_only",
        "trade_count": 1,
        "buy_taker_quote_volume": float("nan"),
        "sell_taker_quote_volume": 0,
        "taker_flow_delta_quote": 0,
        "taker_flow_imbalance": 0,
        "taker_buy_quote_ratio": 0.5,
    })
    assert flow["ok"] is False
    assert flow["error"] == "non_finite_aggregate_trade_metrics"


def test_order_flow_rejects_internally_inconsistent_metrics():
    engine = OrderFlowEngine()
    snapshot = {
        "source": "binance_public_read_only",
        "trade_count": 2,
        "buy_taker_quote_volume": 120.0,
        "sell_taker_quote_volume": 80.0,
        "taker_flow_delta_quote": 50.0,
        "taker_flow_imbalance": 0.2,
        "taker_buy_quote_ratio": 0.6,
    }
    result = engine.analyze_trades(snapshot)
    assert result["ok"] is False
    assert result["error"] == "aggregate_trade_metrics_do_not_reconcile"


def test_order_flow_rejects_boolean_trade_counts():
    result = OrderFlowEngine().analyze_trades({
        "source": "binance_public_read_only",
        "trade_count": True,
        "buy_taker_quote_volume": 100.0,
        "sell_taker_quote_volume": 0.0,
        "taker_flow_delta_quote": 100.0,
        "taker_flow_imbalance": 1.0,
        "taker_buy_quote_ratio": 1.0,
    })
    assert result["ok"] is False
    assert result["error"] == "invalid_aggregate_trade_metrics"


def test_liquidity_engine_rejects_non_reconciling_spread_and_depth_metrics():
    snapshot = {
        "source": "binance_public_read_only",
        "symbol": "BTC/USDT",
        "best_bid": 99.0,
        "best_ask": 101.0,
        "mid_price": 100.0,
        "spread_percent": 1.0,
        "bid_depth_quote": 100.0,
        "ask_depth_quote": 100.0,
        "depth_imbalance": 0.0,
        "top5_bid_depth_quote": 50.0,
        "top5_ask_depth_quote": 50.0,
    }
    result = LiquidityAnalysisEngine().analyze_order_book(snapshot)
    assert result["ok"] is False
    assert result["error"] == "order_book_metrics_do_not_reconcile"


def test_microstructure_engines_accept_market_matched_futures_public_snapshots():
    liquidity = LiquidityAnalysisEngine().analyze_order_book({
        "source": "binance_futures_public_read_only",
        "symbol": "BTC/USDT",
        "best_bid": 99.0, "best_ask": 101.0, "mid_price": 100.0,
        "spread_percent": 2.0, "bid_depth_quote": 100.0,
        "ask_depth_quote": 100.0, "depth_imbalance": 0.0,
    })
    assert liquidity["ok"] is True
    assert liquidity["source"] == "binance_futures_public_read_only"

    flow = OrderFlowEngine().analyze_trades({
        "source": "binance_futures_public_read_only",
        "trade_count": 2,
        "buy_taker_quote_volume": 120.0,
        "sell_taker_quote_volume": 80.0,
        "taker_flow_delta_quote": 40.0,
        "taker_flow_imbalance": 0.2,
        "taker_buy_quote_ratio": 0.6,
    })
    assert flow["ok"] is True
    assert flow["source"] == "binance_futures_public_read_only"


def test_public_aggregate_trade_snapshot_rejects_stale_and_fractional_timestamps(monkeypatch):
    import time
    now_ms = int(time.time() * 1000)
    monkeypatch.setattr("app.engines.market_data.time.time", lambda: now_ms / 1000.0)
    monkeypatch.setattr(BinancePublicData, "get_aggregate_trades", lambda self, symbol, limit: [
        {"p": "100", "q": "2", "T": now_ms - 600_000, "m": False},
        {"p": "101", "q": "1", "T": now_ms + 1.5, "m": True},
    ])
    with pytest.raises(RuntimeError, match="public_aggregate_trades_stale_or_contain_no_valid_rows"):
        MarketDataEngine().get_public_order_flow("BTC/USDT", 2)


def test_public_order_book_rejects_boolean_price_and_quantity_levels(monkeypatch):
    monkeypatch.setattr(BinancePublicData, "get_order_book", lambda self, symbol, limit: {
        "bids": [[True, "2"]], "asks": [["101", True]],
    })
    with pytest.raises(RuntimeError, match="public_order_book_missing_valid_bid_or_ask"):
        MarketDataEngine().get_public_order_book("BTC/USDT", 20)


def test_liquidity_and_orderflow_engines_reject_boolean_numeric_metrics():
    liquidity = LiquidityAnalysisEngine().analyze_order_book({
        "source": "binance_public_read_only",
        "best_bid": True, "best_ask": 101.0, "mid_price": 100.5,
        "spread_percent": 1.99, "bid_depth_quote": 100.0,
        "ask_depth_quote": 100.0, "depth_imbalance": 0.0,
    })
    assert liquidity["ok"] is False
    assert liquidity["error"] == "boolean_order_book_metric_not_allowed"

    flow = OrderFlowEngine().analyze_trades({
        "source": "binance_public_read_only",
        "trade_count": 1,
        "buy_taker_quote_volume": True,
        "sell_taker_quote_volume": 0.0,
        "taker_flow_delta_quote": 100.0,
        "taker_flow_imbalance": 1.0,
        "taker_buy_quote_ratio": 1.0,
    })
    assert flow["ok"] is False
    assert flow["error"] == "boolean_aggregate_trade_metric_not_allowed"
