from app.core.enums import MarketType
from app.engines.risk_simulation import RiskSimulationEngine


def test_daily_loss_limit_blocks_new_simulated_entries_until_next_session():
    result = RiskSimulationEngine().run(
        [
            {"session": "day-1", "entry": 100, "stop_loss": 95, "realized_pnl": -40},
            {"session": "day-1", "entry": 100, "stop_loss": 95, "realized_pnl": -40},
            {"session": "day-1", "entry": 100, "stop_loss": 95, "realized_pnl": 25},
            {"session": "day-2", "entry": 100, "stop_loss": 95, "realized_pnl": 10},
        ],
        initial_balance=1000,
        market=MarketType.SPOT,
        risk_per_trade=0.01,
        max_daily_loss=0.07,
    )
    assert result["ok"] is True
    assert [row["accepted"] for row in result["results"]] == [True, True, False, True]
    assert result["rejected_trades"] == 1
    assert result["final_equity"] == 930
    assert result["no_live_orders"] is True


def test_simulator_rejects_position_count_and_exposure_breaches():
    result = RiskSimulationEngine().run(
        [
            {"entry": 100, "stop_loss": 95, "open_positions": 1},
            {"entry": 100, "stop_loss": 95, "current_exposure": 990},
        ],
        initial_balance=1000,
        market=MarketType.FUTURES,
        max_open_positions=1,
        max_exposure=1000,
    )
    assert result["ok"] is True
    assert all(not row["accepted"] for row in result["results"])
    assert "max_open_positions_reached" in result["results"][0]["issues"]
    assert "max_exposure_exceeded" in result["results"][1]["issues"]


def test_spot_simulation_uses_spot_notional_cap_and_ignores_leverage_request():
    result = RiskSimulationEngine().run(
        [{"entry": 100, "stop_loss": 99, "leverage": 8, "realized_pnl": 0}],
        initial_balance=10000,
        market=MarketType.SPOT,
        risk_per_trade=0.01,
    )
    assert result["ok"] is True
    assert result["results"][0]["accepted"] is True
    assert result["results"][0]["position_notional"] <= 100


def test_simulation_rejects_invalid_inputs_and_never_sends_live_orders():
    simulator = RiskSimulationEngine()
    invalid = simulator.run([], initial_balance=float("nan"), market=MarketType.SPOT)
    assert invalid["ok"] is False
    assert invalid["no_live_orders"] is True

    invalid_trade = simulator.run(
        [{"entry": 100, "stop_loss": 101, "side": "long"}],
        initial_balance=1000,
        market=MarketType.SPOT,
    )
    assert invalid_trade["ok"] is True
    assert invalid_trade["results"][0]["accepted"] is False
    assert "stop_loss_wrong_side_for_position" in invalid_trade["results"][0]["issues"]

def test_historical_price_path_uses_next_open_stops_fees_and_no_lookahead():
    candles = []
    for i in range(60):
        price = 100 + i * 0.8
        candles.append({
            "timestamp": 1_800_000_000_000 + i * 300_000,
            "open": price, "high": price + 1.0, "low": price - 0.5,
            "close": price + 0.6, "volume": 1000,
        })
    result = RiskSimulationEngine().run_historical_price_path(
        candles, initial_balance=1000, market=MarketType.SPOT,
        risk_per_trade=0.005, max_daily_loss=0.07,
        fee_rate=0.001, slippage_rate=0.0005, max_notional=80,
    )
    assert result["ok"] is True
    assert result["total_trades"] >= 1
    assert result["fees_paid"] > 0
    assert result["slippage_cost"] > 0
    assert result["no_lookahead"] is True
    assert result["no_live_orders"] is True
    assert result["fill_model"].startswith("next_open")


def test_historical_price_path_rejects_bad_candle_order_and_parameters():
    candles = [{"timestamp": i, "open": 10, "high": 11, "low": 9,
                "close": 10, "volume": 1} for i in range(30)]
    candles[10]["low"] = 12
    result = RiskSimulationEngine().run_historical_price_path(
        candles, initial_balance=1000, market=MarketType.SPOT,
    )
    assert result["ok"] is False
    assert "invalid_or_unsorted_candle" in result["error"]


def test_futures_historical_price_path_supports_short_positions_and_funding():
    candles=[]
    start=1_800_000_000_000
    for i in range(70):
        price=200-i*0.8
        candles.append({"timestamp":start+i*300_000,"open":price,
            "high":price+0.5,"low":price-1.0,"close":price-0.6,"volume":1000})
    result=RiskSimulationEngine().run_historical_price_path(
        candles,initial_balance=1000,market=MarketType.FUTURES,
        risk_per_trade=0.005,max_daily_loss=0.07,
        fee_rate=0.0004,slippage_rate=0.0002,max_notional=800,
        leverage=2,funding_events=[{"timestamp":start+22*300_000,"rate":0.001}],
    )
    assert result["ok"] is True
    assert result["total_trades"]>=1
    assert any(trade["side"]=="short" for trade in result["trades"])
    assert result["funding_net"]>0
    assert result["no_lookahead"] is True
    assert result["no_live_orders"] is True


def test_spot_historical_price_path_rejects_futures_funding_and_leverage():
    candles=[{"timestamp":i+1,"open":100,"high":102,"low":99,"close":101,"volume":10} for i in range(30)]
    simulator=RiskSimulationEngine()
    bad_funding=simulator.run_historical_price_path(
        candles,initial_balance=1000,market=MarketType.SPOT,
        funding_events=[{"timestamp":20,"rate":0.001}],
    )
    assert bad_funding["ok"] is False
    assert bad_funding["error"]=="spot_funding_events_not_supported"


def test_futures_historical_path_models_isolated_liquidation_conservatively():
    start = 1_800_000_000_000
    candles = []
    for i in range(65):
        price = 100 + i
        if i >= 22:
            price = 121 - (i - 22) * 2.5
        candles.append({
            "timestamp": start + i * 300_000,
            "open": price,
            "high": price + 0.5,
            "low": price - 0.5,
            "close": price + 0.2,
            "volume": 1000,
        })
    # A large intrabar gap crosses both the protective stop and liquidation price.
    candles[22].update({"open": 121, "high": 122, "low": 70, "close": 80})
    result = RiskSimulationEngine().run_historical_price_path(
        candles, initial_balance=1000, market=MarketType.FUTURES,
        risk_per_trade=0.005, max_daily_loss=0.07, fee_rate=0.0004,
        slippage_rate=0.0001, max_notional=800, leverage=5,
        maintenance_margin_rate=0.005, liquidation_fee_rate=0.005,
    )
    assert result["ok"] is True
    assert result["liquidation_model"].startswith("approximate_isolated_margin")
    assert result["cross_margin_mode"] == "not_simulated"
    assert result["liquidation_count"] >= 1
    liquidated = next(t for t in result["trades"] if t["exit_reason"] == "liquidation")
    assert liquidated["liquidation_price"] > 0
    assert liquidated["liquidation_fee"] > 0
    assert liquidated["fees"] >= liquidated["liquidation_fee"]
    assert result["no_live_orders"] is True


def test_futures_historical_path_rejects_impossible_maintenance_margin_rate():
    candles = [{"timestamp": i + 1, "open": 100, "high": 102, "low": 99,
                "close": 101, "volume": 10} for i in range(30)]
    result = RiskSimulationEngine().run_historical_price_path(
        candles, initial_balance=1000, market=MarketType.FUTURES,
        leverage=5, maintenance_margin_rate=0.25,
    )
    assert result["ok"] is False
    assert result["error"] == "price_path_parameters_out_of_range"
    assert result["no_live_orders"] is True


def test_historical_risk_replay_stress_handles_five_thousand_candles_and_trade_cap():
    import math
    start = 1_800_000_000_000
    candles = []
    for index in range(5000):
        close = 100 + index * 0.002 + 4 * math.sin(index / 18)
        open_price = close - 0.15 * math.sin(index / 5)
        candles.append({
            "timestamp": start + index * 60_000,
            "open": open_price,
            "high": max(open_price, close) + 0.35,
            "low": min(open_price, close) - 0.35,
            "close": close,
            "volume": 1000 + (index % 13) * 10,
        })
    result = RiskSimulationEngine().run_historical_price_path(
        candles, initial_balance=1000, market=MarketType.FUTURES,
        risk_per_trade=0.005, max_daily_loss=0.07, fee_rate=0.0004,
        slippage_rate=0.0002, max_notional=800, leverage=3,
        max_trades=50,
    )
    assert result["ok"] is True
    assert len(result["equity_curve"]) == len(candles) + 1
    assert result["total_trades"] <= 50
    assert result["no_lookahead"] is True
    assert result["no_live_orders"] is True


def test_historical_price_path_rejects_fractional_candle_and_funding_timestamps():
    candles = [
        {"timestamp": i + 1, "open": 100, "high": 102, "low": 99,
         "close": 101, "volume": 10}
        for i in range(30)
    ]
    candles[10]["timestamp"] = 11.5
    invalid_candle = RiskSimulationEngine().run_historical_price_path(
        candles, initial_balance=1000, market=MarketType.SPOT,
    )
    assert invalid_candle["ok"] is False
    assert invalid_candle["error"] == "invalid_candle_fields_10"
    assert invalid_candle["no_live_orders"] is True

    valid_candles = [
        {"timestamp": i + 1, "open": 100, "high": 102, "low": 99,
         "close": 101, "volume": 10}
        for i in range(30)
    ]
    invalid_funding = RiskSimulationEngine().run_historical_price_path(
        valid_candles, initial_balance=1000, market=MarketType.FUTURES,
        leverage=2, max_notional=800,
        funding_events=[{"timestamp": 20.5, "rate": 0.001}],
    )
    assert invalid_funding["ok"] is False
    assert invalid_funding["error"] == "invalid_funding_event_0"
    assert invalid_funding["no_live_orders"] is True


def test_risk_simulation_rejects_malformed_exposure_without_raising():
    result = RiskSimulationEngine().run(
        [{"entry": 100, "stop_loss": 95, "current_exposure": "invalid"}],
        initial_balance=1000, market=MarketType.FUTURES,
    )
    assert result["ok"] is False
    assert result["error"] == "trade_0_invalid_numeric_fields"
    assert result["no_live_orders"] is True


def test_risk_simulation_rejects_boolean_parameters_and_non_sequence_trades():
    simulator = RiskSimulationEngine()
    boolean_balance = simulator.run(
        [], initial_balance=True, market=MarketType.SPOT,
    )
    invalid_trades = simulator.run(
        None, initial_balance=1000, market=MarketType.SPOT,
    )
    assert boolean_balance["error"] == "boolean_simulation_parameter_not_allowed"
    assert invalid_trades["error"] == "trades_must_be_a_sequence"
    assert boolean_balance["no_live_orders"] is True
    assert invalid_trades["no_live_orders"] is True


def test_risk_simulation_rejects_boolean_trade_numbers_and_exposure_cap():
    simulator = RiskSimulationEngine()
    boolean_trade = simulator.run(
        [{"entry": True, "stop_loss": 95}],
        initial_balance=1000, market=MarketType.FUTURES,
    )
    boolean_cap = simulator.run(
        [], initial_balance=1000, market=MarketType.FUTURES,
        max_exposure=True,
    )
    assert boolean_trade["error"] == "trade_0_invalid_numeric_fields"
    assert boolean_cap["error"] == "invalid_max_exposure"
    assert boolean_trade["no_live_orders"] is True
    assert boolean_cap["no_live_orders"] is True
