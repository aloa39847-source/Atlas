from datetime import datetime, timezone

from app.engines.specialized_backtest import SpecializedBacktestEngine


def _orderflow_events():
    return [
        {"timestamp": 1000, "symbol": "BTCUSDT", "best_bid": 99, "best_ask": 100,
         "bid_depth_quote": 1000, "ask_depth_quote": 1000,
         "depth_imbalance": 0.2, "taker_flow_imbalance": 0.3},
        {"timestamp": 2000, "symbol": "BTCUSDT", "best_bid": 100, "best_ask": 101,
         "bid_depth_quote": 1000, "ask_depth_quote": 1000,
         "depth_imbalance": 0.3, "taker_flow_imbalance": 0.4},
        {"timestamp": 3000, "symbol": "BTCUSDT", "best_bid": 110, "best_ask": 111,
         "bid_depth_quote": 1000, "ask_depth_quote": 1000,
         "depth_imbalance": 0.1, "taker_flow_imbalance": 0.2},
    ]


def test_orderflow_replay_uses_next_event_and_depth_capped_fills():
    engine = SpecializedBacktestEngine(
        initial_cash=1000, fee_rate=0, slippage_rate=0, max_notional=80,
    )
    result = engine.run_orderflow_replay(
        _orderflow_events(),
        [{"index": 0, "action": "buy"}, {"index": 1, "action": "sell"}],
    )
    assert result["ok"] is True
    assert result["strategy_key"] == "liquidity_orderflow"
    assert result["trades"][0]["entry_index"] == 1
    assert result["trades"][0]["exit_index"] == 2
    assert result["trades"][0]["entry_price"] == 101
    assert result["trades"][0]["exit_price"] == 110
    assert result["trades"][0]["net_pnl"] > 0
    assert result["execution"] == "simulated_only"
    assert result["no_live_orders"] is True
    assert result["no_lookahead"] is False


def test_orderflow_replay_rejects_mixed_symbols_and_bad_depth():
    engine = SpecializedBacktestEngine()
    events = _orderflow_events()
    events[2]["symbol"] = "ETHUSDT"
    assert engine.run_orderflow_replay(events, [])["error"] == "invalid_orderflow_event_2:orderflow_replay_symbol_mismatch"

    events = _orderflow_events()
    events[1]["ask_depth_quote"] = -1
    assert "invalid_orderflow_event_1" in engine.run_orderflow_replay(events, [])["error"]


def test_orderflow_replay_rejects_duplicate_actions_for_one_event():
    result = SpecializedBacktestEngine().run_orderflow_replay(
        _orderflow_events(),
        [{"index": 0, "action": "buy"}, {"index": 0, "action": "sell"}],
    )
    assert result["ok"] is False
    assert result["error"] == "multiple_orderflow_actions_same_event"


def test_triangular_arbitrage_replay_compounds_only_qualified_cycles():
    stamp = datetime(2026, 11, 10, tzinfo=timezone.utc)
    iso = stamp.isoformat()
    epoch = int(stamp.timestamp())
    quotes = {
        "BTCUSDT": {"symbol": "BTCUSDT", "best_bid": 50000, "best_ask": 50100,
                    "fetched_at": iso, "source": "binance_public_read_only"},
        "ETHBTC": {"symbol": "ETHBTC", "best_bid": 0.05, "best_ask": 0.051,
                   "fetched_at": iso, "source": "binance_public_read_only"},
        "ETHUSDT": {"symbol": "ETHUSDT", "best_bid": 2600, "best_ask": 2610,
                    "fetched_at": iso, "source": "binance_public_read_only"},
    }
    result = SpecializedBacktestEngine(
        initial_cash=100, fee_rate=0.0001, slippage_rate=0.0001,
    ).run_triangular_arbitrage_replay(
        [{"timestamp": epoch, "quotes": quotes}],
        route=["USDT", "BTC", "ETH", "USDT"],
        min_profit_pct=0.01,
    )
    assert result["ok"] is True
    assert result["strategy_key"] == "arbitrage"
    assert result["total_trades"] == 1
    assert result["final_equity"] > 100
    assert result["execution"] == "simulated_only"
    assert result["no_live_orders"] is True


def test_triangular_arbitrage_replay_rejects_unsynchronized_or_stale_data():
    stamp = datetime(2026, 11, 10, tzinfo=timezone.utc)
    latest = datetime(2026, 11, 10, 0, 0, 20, tzinfo=timezone.utc)
    iso = stamp.isoformat()
    quotes = {
        "BTCUSDT": {"symbol": "BTCUSDT", "best_bid": 50000, "best_ask": 50100,
                    "fetched_at": iso, "source": "binance_public_read_only"},
        "ETHBTC": {"symbol": "ETHBTC", "best_bid": 0.05, "best_ask": 0.051,
                   "fetched_at": iso, "source": "binance_public_read_only"},
        "ETHUSDT": {"symbol": "ETHUSDT", "best_bid": 2600, "best_ask": 2610,
                    "fetched_at": latest.isoformat(),
                    "source": "binance_public_read_only"},
    }
    result = SpecializedBacktestEngine().run_triangular_arbitrage_replay(
        [{"timestamp": int(latest.timestamp()), "quotes": quotes}],
        route=["USDT", "BTC", "ETH", "USDT"],
    )
    assert result["ok"] is False
    assert "quote_snapshots_too_far_apart" in result["error"]


def test_orderflow_replay_allocates_entry_fees_proportionally_on_partial_final_fill():
    events = _orderflow_events()
    events[-1]["bid_depth_quote"] = 20
    result = SpecializedBacktestEngine(
        initial_cash=1000, fee_rate=0.01, slippage_rate=0, max_notional=80,
    ).run_orderflow_replay(
        events, [{"index": 0, "action": "buy"}],
    )
    assert result["ok"] is True
    trade = result["trades"][0]
    assert trade["exit_reason"] == "end_of_data"
    assert trade["quantity"] < 80 / trade["entry_price"]
    assert trade["fees"] < 0.792 + 0.2

def test_futures_orderbook_replay_generates_causal_signals_and_caps_notional():
    snapshots = []
    for i in range(12):
        mid = 100 + i * 0.1
        imbalance = 0.45 if i < 7 else -0.4
        snapshots.append({
            "timestamp": 1_800_000_000_000 + i * 1000,
            "symbol": "BTCUSDT",
            "quotes": {"symbol": {
                "symbol": "BTCUSDT", "best_bid": mid - 0.05, "best_ask": mid + 0.05,
                "bid_depth_quote": 2000, "ask_depth_quote": 2000,
                "depth_imbalance": imbalance,
            }},
        })
    result = SpecializedBacktestEngine(
        initial_cash=1000, fee_rate=0.0004, slippage_rate=0.0002, max_notional=800,
    ).run_futures_orderbook_replay(
        snapshots, rolling_window=3, entry_imbalance=0.2,
        exit_imbalance=0.02, leverage=2, notional=800,
    )
    assert result["ok"] is True
    assert result["market_type"] == "futures"
    assert result["symbol"] == "BTCUSDT"
    assert result["depth_snapshots"] == 12
    assert result["no_lookahead"] is True
    assert result["no_live_orders"] is True
    assert "prospectively" in result["limitations"][0]


def test_futures_orderbook_replay_rejects_unsorted_snapshots():
    snapshots = []
    for i in range(8):
        mid=100+i
        snapshots.append({"timestamp":1000+i,"symbol":"BTCUSDT","quotes":{"symbol":{
            "symbol":"BTCUSDT","best_bid":mid-0.1,"best_ask":mid+0.1,
            "bid_depth_quote":100,"ask_depth_quote":100,"depth_imbalance":0.2}}})
    snapshots[5]["timestamp"]=snapshots[4]["timestamp"]
    result=SpecializedBacktestEngine().run_futures_orderbook_replay(snapshots)
    assert result["ok"] is False
    assert result["error"] == "invalid_futures_orderbook_values_5"


def _arb_depth_snapshot(timestamp, btc_ask_quote=100000, ethbtc_ask_quote=100, ethusdt_bid_qty=10):
    return {
        "timestamp": timestamp,
        "quotes": {
            "BTCUSDT": {"source":"binance_public_read_only","symbol":"BTCUSDT",
                "bids":[[49990,10]],"asks":[[50000,btc_ask_quote/50000]]},
            "ETHBTC": {"source":"binance_public_read_only","symbol":"ETHBTC",
                "bids":[[0.0499,10]],"asks":[[0.05,ethbtc_ask_quote/0.05]]},
            "ETHUSDT": {"source":"binance_public_read_only","symbol":"ETHUSDT",
                "bids":[[2600,ethusdt_bid_qty]],"asks":[[2601,10]]},
        },
    }


def test_triangular_execution_replay_models_latency_depth_and_exchange_filters():
    snapshots = [
        _arb_depth_snapshot(1_800_000_000_000),
        _arb_depth_snapshot(1_800_000_001_000),
        _arb_depth_snapshot(1_800_000_002_000),
    ]
    result = SpecializedBacktestEngine(
        initial_cash=1000, fee_rate=0.0001, slippage_rate=0,
    ).run_triangular_execution_replay(
        snapshots, route=["USDT","BTC","ETH","USDT"],
        min_profit_pct=0.01, latency_ms=1000,
        symbol_filters={
            "BTCUSDT":{"tick_size":0.1,"step_size":0.00001,"min_qty":0.00001,"min_notional":5},
            "ETHBTC":{"tick_size":0.00001,"step_size":0.001,"min_qty":0.001,"min_notional":0.0001},
            "ETHUSDT":{"tick_size":0.01,"step_size":0.001,"min_qty":0.001,"min_notional":5},
        },
    )
    assert result["ok"] is True
    assert result["total_trades"] == 1
    assert result["cycles"][0]["latency_ms"] == 1000
    assert result["cycles"][0]["legs"][0]["complete"] is True
    assert result["exchange_filters_supplied"] is True
    assert result["no_live_orders"] is True


def test_triangular_execution_replay_reports_partial_depth_fills():
    snapshots = [
        _arb_depth_snapshot(1_800_000_000_000),
        _arb_depth_snapshot(1_800_000_001_000, btc_ask_quote=10),
    ]
    result = SpecializedBacktestEngine(
        initial_cash=1000, fee_rate=0, slippage_rate=0,
    ).run_triangular_execution_replay(
        snapshots, route=["USDT","BTC","ETH","USDT"],
        min_profit_pct=0.01, latency_ms=1000,
    )
    assert result["ok"] is True
    assert result["total_trades"] == 0
    assert result["partial_fills"] == 1
    assert result["partial_fill_details"][0]["unfilled_input"] > 0


def test_triangular_execution_replay_applies_and_reports_adverse_slippage_and_fees():
    snapshots = [
        _arb_depth_snapshot(1_800_000_000_000),
        _arb_depth_snapshot(1_800_000_001_000),
    ]
    result = SpecializedBacktestEngine(
        initial_cash=1000, fee_rate=0.0001, slippage_rate=0.001,
    ).run_triangular_execution_replay(
        snapshots, route=["USDT", "BTC", "ETH", "USDT"],
        min_profit_pct=0.01, latency_ms=1000,
    )
    assert result["ok"] is True
    assert result["total_trades"] == 1
    assert result["slippage_rate_configured"] == 0.001
    assert result["slippage_model"] == "historical_book_depth_plus_configured_adverse_fill_rate"
    assert result["slippage_cost_by_quote_asset"]["USDT"] > 0
    assert result["slippage_cost_by_quote_asset"]["BTC"] > 0
    assert result["fees_paid_by_asset"]["BTC"] > 0
    assert result["fees_paid_by_asset"]["ETH"] > 0
    assert result["fees_paid_by_asset"]["USDT"] > 0
    assert result["no_live_orders"] is True


def test_triangular_execution_replay_rejects_fractional_and_boolean_timestamps():
    snapshots = [
        _arb_depth_snapshot(1_800_000_000_000),
        _arb_depth_snapshot(1_800_000_001_000),
    ]
    snapshots[1]["timestamp"] = 1_800_000_001_000.5
    fractional = SpecializedBacktestEngine().run_triangular_execution_replay(
        snapshots, route=["USDT", "BTC", "ETH", "USDT"],
    )
    snapshots[1]["timestamp"] = True
    boolean = SpecializedBacktestEngine().run_triangular_execution_replay(
        snapshots, route=["USDT", "BTC", "ETH", "USDT"],
    )
    assert fractional["ok"] is False
    assert fractional["error"] == "invalid_execution_snapshot_timestamp_1"
    assert boolean["ok"] is False
    assert boolean["error"] == "invalid_execution_snapshot_timestamp_1"
    assert fractional["no_live_orders"] is True
    assert boolean["no_live_orders"] is True


def test_triangular_execution_replay_aggregates_exchange_minimum_quantity_across_levels():
    snapshots = [
        _arb_depth_snapshot(1_800_000_000_000),
        _arb_depth_snapshot(1_800_000_001_000),
    ]
    snapshots[1]["quotes"]["BTCUSDT"]["asks"] = [
        [50000, 0.000005], [50010, 0.000005],
    ]
    result = SpecializedBacktestEngine(
        initial_cash=1000, fee_rate=0, slippage_rate=0,
    ).run_triangular_execution_replay(
        snapshots, route=["USDT", "BTC", "ETH", "USDT"],
        min_profit_pct=0.01, latency_ms=1000,
        symbol_filters={
            "BTCUSDT": {"step_size": 0.000005, "min_qty": 0.00001, "min_notional": 0.1},
        },
    )
    assert result["ok"] is True
    assert result["total_trades"] == 0
    assert result["partial_fills"] == 1
    assert result["partial_fill_details"][0]["filled_quantity"] == 0.00001
    assert result["partial_fill_details"][0]["reason"] == "insufficient_depth"


def test_triangular_execution_replay_rejects_invalid_filter_container():
    snapshots = [
        _arb_depth_snapshot(1_800_000_000_000),
        _arb_depth_snapshot(1_800_000_001_000),
    ]
    result = SpecializedBacktestEngine().run_triangular_execution_replay(
        snapshots, route=["USDT", "BTC", "ETH", "USDT"],
        symbol_filters=["not", "a", "mapping"],
    )
    assert result["ok"] is False
    assert result["error"] == "symbol_filters_must_be_a_mapping"


def test_triangular_execution_replay_enforces_exchange_max_quantity():
    snapshots = [
        _arb_depth_snapshot(1_800_000_000_000),
        _arb_depth_snapshot(1_800_000_001_000),
    ]
    result = SpecializedBacktestEngine(
        initial_cash=1000, fee_rate=0, slippage_rate=0,
    ).run_triangular_execution_replay(
        snapshots, route=["USDT", "BTC", "ETH", "USDT"],
        min_profit_pct=0.01, latency_ms=1000,
        symbol_filters={"BTCUSDT": {"step_size": 0.00001, "max_qty": 0.00001}},
    )
    assert result["ok"] is True
    assert result["total_trades"] == 0
    assert result["partial_fills"] == 1
    assert result["partial_fill_details"][0]["reason"] == "exchange_max_qty_reached"
    assert result["partial_fill_details"][0]["filled_quantity"] == 0.00001
