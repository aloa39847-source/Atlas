from app.core.enums import EnvironmentMode, MarketType, Timeframe
from app.engines.decision import DecisionEngine
from app.engines.technical import TimeframeAnalysis


def _analysis(timeframe, trend, signals):
    return TimeframeAnalysis(
        timeframe=timeframe,
        trend=trend,
        signals=list(signals),
        indicators={"close": 100.0, "atr14": 1.0},
        structure={"bias": "neutral", "breakouts": []},
        candles={"patterns": []},
    )


def _complete_timeframes(analyses):
    completed = dict(analyses)
    for timeframe in Timeframe:
        completed.setdefault(timeframe, _analysis(timeframe, "sideways", []))
    return completed


def test_decision_waits_when_entry_timeframe_conflicts_with_higher_timeframes():
    engine = DecisionEngine()
    engine.hybrid = None
    engine.multi_agent = None
    analyses = {
        Timeframe.D1: _analysis(Timeframe.D1, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.H4: _analysis(Timeframe.H4, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.H1: _analysis(Timeframe.H1, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.M15: _analysis(Timeframe.M15, "bearish", ["price_below_ema20_ema50", "macd_hist_negative"]),
        Timeframe.M5: _analysis(Timeframe.M5, "sideways", []),
    }
    result = engine.decide(
        symbol="BTC/USDT", market=MarketType.SPOT, environment=EnvironmentMode.DEMO,
        timeframe_analyses=_complete_timeframes(analyses), balance=150.0, max_leverage=1.0,
        risk_per_trade=0.005,
    )
    assert result["bias"] == "bullish"
    assert result["entry_timeframe"] == "15m"
    assert result["entry_timeframe_aligned"] is False
    assert result["action"] == "wait"
    assert "conflicts" in result["reason"]


def test_decision_allows_direction_only_when_selected_entry_timeframe_aligns():
    engine = DecisionEngine()
    engine.hybrid = None
    engine.multi_agent = None
    analyses = {
        Timeframe.D1: _analysis(Timeframe.D1, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.H4: _analysis(Timeframe.H4, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.H1: _analysis(Timeframe.H1, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.M15: _analysis(Timeframe.M15, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.M5: _analysis(Timeframe.M5, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
    }
    result = engine.decide(
        symbol="BTC/USDT", market=MarketType.SPOT, environment=EnvironmentMode.DEMO,
        timeframe_analyses=_complete_timeframes(analyses), balance=150.0, max_leverage=1.0,
        risk_per_trade=0.005,
    )
    assert result["entry_timeframe_aligned"] is True
    assert result["action"] == "buy"


def test_decision_waits_when_any_approved_timeframe_is_missing():
    engine = DecisionEngine()
    engine.hybrid = None
    engine.multi_agent = None
    analyses = {
        Timeframe.D1: _analysis(Timeframe.D1, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.H4: _analysis(Timeframe.H4, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.H1: _analysis(Timeframe.H1, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.M15: _analysis(Timeframe.M15, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        # M5 intentionally absent: the approved five-timeframe stack is incomplete.
    }
    result = engine.decide(
        symbol="BTC/USDT", market=MarketType.SPOT, environment=EnvironmentMode.DEMO,
        timeframe_analyses=analyses, balance=150.0, max_leverage=1.0,
        risk_per_trade=0.005,
    )
    assert result["action"] == "wait"
    assert result["reason"] == "required_timeframe_data_missing"
    assert "5m" in result["extra"]["missing_timeframes"]
    assert result["extra"]["missing_timeframes"] == ["5m"]


def test_optional_higher_timeframes_veto_when_two_or_more_oppose_primary_bias():
    engine = DecisionEngine()
    engine.hybrid = None
    engine.multi_agent = None
    analyses = {
        Timeframe.D1: _analysis(Timeframe.D1, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.H4: _analysis(Timeframe.H4, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.H1: _analysis(Timeframe.H1, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.M15: _analysis(Timeframe.M15, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.M5: _analysis(Timeframe.M5, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.W1: _analysis(Timeframe.W1, "bearish", []),
        Timeframe.D3: _analysis(Timeframe.D3, "bearish", []),
    }
    result = engine.decide(
        symbol="BTC/USDT", market=MarketType.SPOT, environment=EnvironmentMode.DEMO,
        timeframe_analyses=_complete_timeframes(analyses), balance=150.0, max_leverage=1.0,
        risk_per_trade=0.005,
    )
    assert result["action"] == "wait"
    assert result["reason"] == "extended_timeframe_conflict"
    assert result["extra"]["extended_timeframe_context"]["1w"] == "bearish"
    assert result["extra"]["extended_timeframe_context"]["3d"] == "bearish"


def test_single_opposing_optional_higher_timeframe_does_not_veto():
    engine = DecisionEngine()
    engine.hybrid = None
    engine.multi_agent = None
    analyses = {
        Timeframe.D1: _analysis(Timeframe.D1, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.H4: _analysis(Timeframe.H4, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.H1: _analysis(Timeframe.H1, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.M15: _analysis(Timeframe.M15, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.M5: _analysis(Timeframe.M5, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.W1: _analysis(Timeframe.W1, "bearish", []),
    }
    result = engine.decide(
        symbol="BTC/USDT", market=MarketType.SPOT, environment=EnvironmentMode.DEMO,
        timeframe_analyses=_complete_timeframes(analyses), balance=150.0, max_leverage=1.0,
        risk_per_trade=0.005,
    )
    assert result["action"] == "buy"
    assert result["extended_timeframe_context"]["1w"] == "bearish"
    assert result["strategy_evidence_score"] >= 3



def test_optional_timeframes_are_not_required_for_a_valid_decision():
    engine = DecisionEngine()
    engine.hybrid = None
    engine.multi_agent = None
    directional_signals = ["price_above_ema20_ema50", "macd_hist_positive"]
    analyses = _complete_timeframes({
        Timeframe.D1: _analysis(Timeframe.D1, "bullish", directional_signals),
        Timeframe.H4: _analysis(Timeframe.H4, "bullish", directional_signals),
        Timeframe.H1: _analysis(Timeframe.H1, "bullish", directional_signals),
        Timeframe.M15: _analysis(Timeframe.M15, "bullish", directional_signals),
        Timeframe.M5: _analysis(Timeframe.M5, "bullish", directional_signals),
    })
    analyses[Timeframe.M1] = None
    result = engine.decide(
        symbol="BTC/USDT", market=MarketType.SPOT, environment=EnvironmentMode.DEMO,
        timeframe_analyses=analyses, balance=150.0, max_leverage=1.0,
        risk_per_trade=0.005,
    )
    assert result["action"] == "buy"
    assert result["timeframe_consensus"]["all_required_frames_present"] is True
    assert result["timeframes_analyzed_required"] == ["1d", "4h", "1h", "15m", "5m"]
    assert "1m" not in result["timeframes"]



def test_independent_risk_engine_vetoes_directional_strategy_signal(monkeypatch):
    engine = DecisionEngine()
    engine.hybrid = None
    engine.multi_agent = None
    analyses = {
        Timeframe.D1: _analysis(Timeframe.D1, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.H4: _analysis(Timeframe.H4, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.H1: _analysis(Timeframe.H1, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.M15: _analysis(Timeframe.M15, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.M5: _analysis(Timeframe.M5, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
    }
    def reject_trade(**kwargs):
        return {"ok": False, "issues": ["daily_loss_limit_reached"], "position_notional_estimate": 0.0}
    monkeypatch.setattr(engine.risk, "validate_trade", reject_trade)
    result = engine.decide(
        symbol="BTC/USDT", market=MarketType.SPOT, environment=EnvironmentMode.DEMO,
        timeframe_analyses=_complete_timeframes(analyses), balance=150.0, max_leverage=1.0,
        risk_per_trade=0.005,
    )
    assert result["action"] == "wait"
    assert "Risk engine blocked the setup" in result["reason"]
    assert "daily_loss_limit_reached" in result["reason"]


def test_decision_engine_invokes_agents_with_final_strategy_risk_and_trade_memory():
    engine = DecisionEngine()
    engine.hybrid = None
    seen = {}

    class RecordingCoordinator:
        def deliberate(self, context):
            seen.update(context)
            return {
                "status": "completed",
                "consensus": "bullish",
                "action_authority": "none",
                "final_decision_owner": "TITAN_DecisionEngine",
            }

    engine.multi_agent = RecordingCoordinator()
    analyses = _complete_timeframes({
        Timeframe.D1: _analysis(Timeframe.D1, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.H4: _analysis(Timeframe.H4, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.H1: _analysis(Timeframe.H1, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.M15: _analysis(Timeframe.M15, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
        Timeframe.M5: _analysis(Timeframe.M5, "bullish", ["price_above_ema20_ema50", "macd_hist_positive"]),
    })
    memory = {
        "trend_following_ema": {
            "sample_count": 35, "win_rate": 0.57,
            "expectancy_per_trade": 1.25,
            "research_guidance": "continue_observation",
            "source": "confirmed_demo_trade_outcomes",
            "research_only": True,
            "auto_tuning": False,
        },
    }
    result = engine.decide(
        symbol="BTC/USDT", market=MarketType.SPOT, environment=EnvironmentMode.DEMO,
        timeframe_analyses=analyses, balance=150.0, max_leverage=1.0,
        risk_per_trade=0.005, strategy_memory=memory,
    )
    assert result["action"] == "buy"
    assert seen["symbol"] == "BTC/USDT"
    assert seen["strategy"]["key"] == result["strategy"]
    assert seen["risk_result"]["ok"] is True
    assert seen["strategy_memory"] == memory
    assert set(seen["timeframe_analyses"]) == {timeframe.value for timeframe in Timeframe}
    assert result["multi_agent_deliberation"]["action_authority"] == "none"
