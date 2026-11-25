from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import db as dbmod
from app.models.entities import OrderRecord, PositionRecord
from app.learning.outcomes import record_closed_demo_trade, summarize_strategy_outcomes


def _db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'learning_outcomes.db'}",
        connect_args={"check_same_thread": False}, future=True,
    )
    monkeypatch.setattr(dbmod, "engine", engine)
    dbmod.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return factory(), engine


def test_closed_demo_trade_is_recorded_net_of_entry_and_exit_fees(tmp_path, monkeypatch):
    db, engine = _db(tmp_path, monkeypatch)
    try:
        entry = OrderRecord(
            environment_mode="demo", market_type="spot", symbol="BTCUSDT",
            side="buy", order_type="market", quantity=1, filled_quantity=1,
            avg_fill_price=100, status="filled", shadow=False, fees_paid=0.1,
        )
        db.add(entry)
        db.flush()
        position = PositionRecord(
            environment_mode="demo", market_type="spot", symbol="BTCUSDT",
            side="long_spot", quantity=0, entry_avg_price=100, mark_price=110,
            realized_pnl=10, status="closed", shadow=False,
            metadata_json={"strategy": "trend_following_ema", "entry_order_id": entry.id},
        )
        exit_order = OrderRecord(
            environment_mode="demo", market_type="spot", symbol="BTCUSDT",
            side="sell", order_type="market", quantity=1, filled_quantity=1,
            avg_fill_price=110, status="filled", shadow=False, fees_paid=0.11,
        )
        db.add_all([position, exit_order])
        db.flush()
        first = record_closed_demo_trade(db, position, exit_order, exit_reason="take_profit")
        db.commit()
        assert first["ok"] is True
        assert first["strategy_key"] == "trend_following_ema"
        assert first["net_pnl"] == 9.79
        assert exit_order.metadata_json["realized_pnl_net"] == 9.79

        duplicate = record_closed_demo_trade(db, position, exit_order, exit_reason="take_profit")
        assert duplicate["ok"] is True
        assert duplicate["already_recorded"] is True
        summary = summarize_strategy_outcomes(db, "trend_following_ema")
        assert summary["sample_count"] == 1
        assert summary["wins"] == 1
        assert summary["net_pnl"] == 9.79
        assert summary["fees_paid"] == 0.21
        assert summary["auto_tuning"] is False
        assert summary["live_config_changed"] is False
    finally:
        db.close()
        engine.dispose()


def test_learning_outcomes_reject_open_positions_unconfirmed_fills_and_live(tmp_path, monkeypatch):
    db, engine = _db(tmp_path, monkeypatch)
    try:
        position = PositionRecord(
            environment_mode="demo", market_type="futures", symbol="ETHUSDT",
            side="short", quantity=1, entry_avg_price=100, realized_pnl=0,
            status="open", shadow=False, metadata_json={"strategy": "momentum_macd_rsi"},
        )
        order = OrderRecord(
            environment_mode="demo", market_type="futures", symbol="ETHUSDT",
            side="buy", order_type="market", quantity=1, filled_quantity=1,
            avg_fill_price=90, status="submitted", shadow=False, fees_paid=0,
        )
        db.add_all([position, order])
        db.flush()
        assert record_closed_demo_trade(db, position, order, exit_reason="signal")["error"] == "fully_closed_position_required"
        position.status = "closed"
        position.quantity = 0
        assert record_closed_demo_trade(db, position, order, exit_reason="signal")["error"] == "confirmed_full_exit_fill_required"
        order.status = "filled"
        position.environment_mode = "live"
        assert record_closed_demo_trade(db, position, order, exit_reason="signal")["error"] == "demo_environment_required"
        assert summarize_strategy_outcomes(db, "momentum_macd_rsi")["sample_count"] == 0
    finally:
        db.close()
        engine.dispose()


def test_strategy_outcome_summary_handles_losses_and_drawdown(tmp_path, monkeypatch):
    db, engine = _db(tmp_path, monkeypatch)
    try:
        for index, pnl in enumerate((5.0, -8.0, 4.0), start=1):
            db.add(__import__("app.models.entities", fromlist=["LearningRecord"]).LearningRecord(
                environment_mode="demo", market_type="spot", category="trade_outcome",
                subject=str(index), outcome="win" if pnl > 0 else "loss",
                lesson="test outcome",
                evidence={"strategy_key": "mean_reversion_bbands", "net_pnl": pnl, "total_fees": 0.2, "source": "binance_demo_confirmed_fills", "research_only": True},
                verified=False,
            ))
        db.commit()
        summary = summarize_strategy_outcomes(db, "mean_reversion_bbands")
        assert summary["sample_count"] == 3
        assert summary["wins"] == 2 and summary["losses"] == 1
        assert summary["net_pnl"] == 1.0
        assert summary["max_drawdown_net_pnl"] == 8.0
        assert summary["expectancy_per_trade"] == round(1 / 3, 10)
    finally:
        db.close()
        engine.dispose()


def test_strategy_outcome_learning_reports_payoff_streak_and_market_symbol_breakdowns(tmp_path, monkeypatch):
    from app.models.entities import LearningRecord

    db, engine = _db(tmp_path, monkeypatch)
    try:
        samples = [
            (5.0, "spot", "BTCUSDT"),
            (-8.0, "spot", "BTCUSDT"),
            (-2.0, "futures", "ETHUSDT"),
            (4.0, "futures", "ETHUSDT"),
        ]
        for index, (pnl, market, symbol) in enumerate(samples, start=1):
            db.add(LearningRecord(
                environment_mode="demo", market_type=market, category="trade_outcome",
                subject=str(index), outcome="win" if pnl > 0 else "loss",
                lesson="confirmed demo result",
                evidence={
                    "strategy_key": "breakout_structure", "net_pnl": pnl,
                    "total_fees": 0.25, "source": "binance_demo_confirmed_fills", "research_only": True, "market_type": market, "symbol": symbol,
                },
                verified=False,
            ))
        db.commit()
        summary = summarize_strategy_outcomes(db, "breakout_structure")
        assert summary["sample_count"] == 4
        assert summary["average_win"] == 4.5
        assert summary["average_loss"] == -5.0
        assert summary["payoff_ratio"] == 0.9
        assert summary["largest_win"] == 5.0
        assert summary["largest_loss"] == -8.0
        assert summary["longest_loss_streak"] == 2
        assert summary["recent_10_expectancy"] == -0.25
        assert summary["market_breakdown"]["spot"]["sample_count"] == 2
        assert summary["symbol_breakdown"]["ETHUSDT"]["net_pnl"] == 2.0
        assert summary["sample_sufficient_for_research"] is False
        assert summary["auto_tuning"] is False
        assert summary["live_config_changed"] is False
    finally:
        db.close()
        engine.dispose()


def test_learning_recommendations_are_sample_gated_and_regime_aware(tmp_path, monkeypatch):
    from app.models.entities import LearningRecord
    db, engine = _db(tmp_path, monkeypatch)
    try:
        samples = [(1.0, "trend", "1h") for _ in range(20)] + [(-2.0, "high_volatility", "15m") for _ in range(10)]
        for index, (pnl, regime, timeframe) in enumerate(samples, start=1):
            db.add(LearningRecord(
                environment_mode="demo", market_type="futures", category="trade_outcome",
                subject=f"regime-{index}", outcome="win" if pnl > 0 else "loss",
                lesson="confirmed demo outcome",
                evidence={
                    "strategy_key": "trend_following_ema", "net_pnl": pnl,
                    "total_fees": 0.1, "source": "binance_demo_confirmed_fills", "research_only": True, "market_type": "futures",
                    "symbol": "BTCUSDT", "market_regime": regime, "timeframe": timeframe,
                }, verified=False,
            ))
        db.commit()
        summary = summarize_strategy_outcomes(db, "trend_following_ema")
        assert summary["sample_count"] == 30
        assert summary["sample_sufficient_for_research"] is True
        assert summary["research_guidance"]["status"] == "recent_deterioration"
        assert summary["regime_breakdown"]["high_volatility"]["sample_count"] == 10
        assert summary["timeframe_breakdown"]["15m"]["sample_count"] == 10
        assert summary["auto_tuning"] is False
        assert summary["live_config_changed"] is False
    finally:
        db.close()
        engine.dispose()


def test_learning_guidance_recommends_review_for_negative_expectancy_without_auto_tuning(tmp_path, monkeypatch):
    from app.models.entities import LearningRecord
    db, engine = _db(tmp_path, monkeypatch)
    try:
        for index in range(30):
            db.add(LearningRecord(
                environment_mode="demo", market_type="spot", category="trade_outcome",
                subject=f"negative-{index}", outcome="loss", lesson="confirmed demo loss",
                evidence={"strategy_key": "mean_reversion_bbands", "net_pnl": -1.0,
                          "total_fees": 0.05, "source": "binance_demo_confirmed_fills", "research_only": True, "market_type": "spot", "symbol": "ETHUSDT"},
                verified=False,
            ))
        db.commit()
        summary = summarize_strategy_outcomes(db, "mean_reversion_bbands")
        assert summary["research_guidance"]["status"] == "negative_expectancy_review"
        assert summary["expectancy_per_trade"] == -1.0
        assert summary["auto_tuning"] is False
        assert summary["live_config_changed"] is False
    finally:
        db.close()
        engine.dispose()


def test_strategy_outcome_summary_ignores_unverified_demo_learning_notes(tmp_path, monkeypatch):
    from app.models.entities import LearningRecord
    db, engine = _db(tmp_path, monkeypatch)
    try:
        db.add_all([
            LearningRecord(environment_mode="demo", market_type="spot", category="trade_outcome",
                subject="verified", outcome="win", lesson="confirmed fill",
                evidence={"strategy_key": "ema", "net_pnl": 2.0, "total_fees": 0.1,
                          "source": "binance_demo_confirmed_fills", "research_only": True}),
            LearningRecord(environment_mode="demo", market_type="spot", category="trade_outcome",
                subject="unverified", outcome="win", lesson="manual note",
                evidence={"strategy_key": "ema", "net_pnl": 900.0, "total_fees": 0.0}),
        ])
        db.commit()
        summary = summarize_strategy_outcomes(db, "ema")
        assert summary["sample_count"] == 1
        assert summary["net_pnl"] == 2.0
    finally:
        db.close()
        engine.dispose()
