from pathlib import Path
import sys

root = Path(sys.argv[1])

p = root / "app/engines/decision.py"
s = p.read_text()
if "risk_context: dict[str, Any] | None = None" not in s:
    old = '''        batches: dict | None = None,
    ) -> dict[str, Any]:
'''
    new = '''        batches: dict | None = None,
        risk_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
'''
    if old not in s:
        raise SystemExit("DecisionEngine risk-context signature marker missing")
    s = s.replace(old, new, 1)
old = '''            risk_result = self.risk.validate_trade(
                environment=environment, market=market, balance=balance, risk_per_trade=risk_per_trade,
                entry=price, stop_loss=stop_loss, leverage=lev, max_leverage=max_leverage,
            )
'''
new = '''            context = risk_context or {}
            risk_result = self.risk.validate_trade(
                environment=environment, market=market, balance=balance, risk_per_trade=risk_per_trade,
                entry=price, stop_loss=stop_loss, leverage=lev, max_leverage=max_leverage,
                daily_loss_used=float(context.get("daily_loss_used", 0.0)),
                max_daily_loss=context.get("max_daily_loss"),
                open_positions=int(context.get("open_positions", 0)),
                max_open_positions=context.get("max_open_positions"),
                current_exposure=float(context.get("current_exposure", 0.0)),
                max_exposure=context.get("max_exposure"),
            )
'''
if new not in s:
    if old not in s:
        raise SystemExit("DecisionEngine risk validation marker missing")
    s = s.replace(old, new, 1)
p.write_text(s)

p = root / "app/services/core_service.py"
s = p.read_text()
s = s.replace(
    "from app.models.entities import DecisionRecord, EngineState, SystemState",
    "from app.models.entities import DecisionRecord, EngineState, SystemState, PositionRecord",
    1,
)
marker = "        batches = self.market_data.get_multi_timeframe(symbol, seed=42, market_type=market.value)\n"
context = '''        state = self._state(db)
        limits = state.risk_limits or {}
        portfolio_positions = db.execute(select(PositionRecord).where(
            PositionRecord.environment_mode == env.value,
            PositionRecord.market_type == market.value,
        )).scalars().all()
        open_positions = [p for p in portfolio_positions if p.status == "open"]
        current_exposure = sum(abs(float(p.mark_price or p.entry_avg_price or 0.0) * float(p.quantity or 0.0)) for p in open_positions)
        today = _utcnow().date()
        daily_realized_losses = sum(max(0.0, -float(p.realized_pnl or 0.0)) for p in portfolio_positions
                                    if p.updated_at and p.updated_at.date() == today)
        daily_unrealized_losses = sum(max(0.0, -float(p.unrealized_pnl or 0.0)) for p in open_positions)
        risk_context = {
            "daily_loss_used": daily_realized_losses + daily_unrealized_losses,
            "max_daily_loss": float(limits.get("max_daily_loss_cap", settings.max_daily_loss_cap)),
            "open_positions": len(open_positions),
            "max_open_positions": int(limits.get("max_open_positions_cap", settings.max_open_positions_cap)),
            "current_exposure": current_exposure,
            "max_exposure": float(limits.get("max_total_exposure", balance * 2.0)),
        }
'''
if "risk_context = {" not in s:
    if marker not in s:
        raise SystemExit("Core service risk-context insertion marker missing")
    s = s.replace(marker, context + marker, 1)
old = '''            balance=balance, max_leverage=max_leverage, risk_per_trade=risk_per_trade,
            batches=batches,
'''
new = '''            balance=balance, max_leverage=max_leverage, risk_per_trade=risk_per_trade,
            batches=batches, risk_context=risk_context,
'''
if new not in s:
    if old not in s:
        raise SystemExit("Core service DecisionEngine call marker missing")
    s = s.replace(old, new, 1)
p.write_text(s)

TESTS = r'''from app.core.enums import EnvironmentMode, MarketType, Timeframe
from app.engines.decision import DecisionEngine
from app.engines.technical import TimeframeAnalysis


def _analyses():
    return {
        tf: TimeframeAnalysis(tf, "bullish", [], {"close": 100.0, "atr14": 1.0}, {}, {})
        for tf in (Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5)
    }


def _decide(context):
    return DecisionEngine().decide(
        symbol="BTC/USDT", market=MarketType.SPOT, environment=EnvironmentMode.DEMO,
        timeframe_analyses=_analyses(), risk_context=context,
    )


def test_decision_blocks_when_max_open_positions_reached():
    result = _decide({"open_positions": 5, "max_open_positions": 5})
    assert result["action"] == "wait"
    assert "max_open_positions_reached" in result["reason"]


def test_decision_blocks_when_daily_loss_limit_reached():
    result = _decide({"daily_loss_used": 50, "max_daily_loss": 0.05})
    assert result["action"] == "wait"
    assert "daily_loss_limit_reached" in result["reason"]


def test_decision_blocks_when_portfolio_exposure_would_be_exceeded():
    result = _decide({"current_exposure": 2000, "max_exposure": 2000})
    assert result["action"] == "wait"
    assert "max_exposure_exceeded" in result["reason"]


def test_decision_allows_when_portfolio_risk_context_is_within_limits():
    result = _decide({"daily_loss_used": 0, "max_daily_loss": 0.05,
                      "open_positions": 0, "max_open_positions": 5,
                      "current_exposure": 0, "max_exposure": 2000})
    assert result["action"] == "buy"
    assert result["risk"]["ok"] is True
'''
(root / "tests/test_portfolio_risk_context.py").write_text(TESTS)
