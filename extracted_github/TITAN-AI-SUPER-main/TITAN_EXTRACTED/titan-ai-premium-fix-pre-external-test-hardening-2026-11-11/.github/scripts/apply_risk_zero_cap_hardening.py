from pathlib import Path
import sys

root = Path(sys.argv[1])

p = root / "app/engines/risk.py"
s = p.read_text()
if "max_leverage_must_be_positive" not in s:
    old = '''        max_lev = min(float(max_leverage or settings.max_leverage_cap), settings.max_leverage_cap)
        max_risk = min(float(max_risk_per_trade or settings.max_risk_per_trade_cap), settings.max_risk_per_trade_cap)
        lev = float(leverage)
'''
    new = '''        requested_max_leverage = settings.max_leverage_cap if max_leverage is None else float(max_leverage)
        if requested_max_leverage <= 0:
            return {"ok": False, "issues": ["max_leverage_must_be_positive"], "position_size": 0.0}
        requested_max_risk = settings.max_risk_per_trade_cap if max_risk_per_trade is None else float(max_risk_per_trade)
        if requested_max_risk <= 0:
            return {"ok": False, "issues": ["max_risk_per_trade_must_be_positive"], "position_size": 0.0}
        max_lev = min(requested_max_leverage, settings.max_leverage_cap)
        max_risk = min(requested_max_risk, settings.max_risk_per_trade_cap)
        lev = float(leverage)
'''
    if old not in s:
        raise SystemExit("Risk cap defaulting marker missing.")
    s = s.replace(old, new, 1)
    old = '''        rpt = min(max(float(risk_per_trade), 0.0), max_risk)
        if rpt <= 0:
            issues.append("risk_per_trade_must_be_positive")
            rpt = min(0.005, max_risk)
'''
    new = '''        rpt = min(max(float(risk_per_trade), 0.0), max_risk)
        if rpt <= 0:
            return {"ok": False, "issues": ["risk_per_trade_must_be_positive"], "position_size": 0.0}
'''
    if old not in s:
        raise SystemExit("Risk-per-trade zero fallback marker missing.")
    p.write_text(s.replace(old, new, 1))

p = root / "app/api/routes.py"
s = p.read_text().replace('Field(..., example="BTC/USDT")', 'Field(..., json_schema_extra={"example": "BTC/USDT"})', 1)
p.write_text(s)

p = root / "tests/test_risk_position_caps.py"
s = p.read_text()
if "test_zero_max_leverage_fails_closed_instead_of_falling_back_to_default" not in s:
    s += '''

def test_zero_max_leverage_fails_closed_instead_of_falling_back_to_default():
    result = RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO, market=MarketType.FUTURES,
        balance=1000, risk_per_trade=0.01, entry=100, stop_loss=99,
        leverage=1, max_leverage=0,
    )
    assert result["ok"] is False
    assert "max_leverage_must_be_positive" in result["issues"]


def test_zero_max_risk_per_trade_fails_closed():
    result = RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO, market=MarketType.SPOT,
        balance=1000, risk_per_trade=0.01, entry=100, stop_loss=99,
        leverage=1, max_leverage=1, max_risk_per_trade=0,
    )
    assert result["ok"] is False
    assert "max_risk_per_trade_must_be_positive" in result["issues"]


def test_zero_requested_risk_per_trade_fails_closed():
    result = RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO, market=MarketType.SPOT,
        balance=1000, risk_per_trade=0, entry=100, stop_loss=99,
        leverage=1, max_leverage=1,
    )
    assert result["ok"] is False
    assert "risk_per_trade_must_be_positive" in result["issues"]
'''
p.write_text(s)
