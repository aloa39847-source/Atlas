from pathlib import Path
import sys

root = Path(sys.argv[1])
p = root / "app/engines/risk.py"
s = p.read_text()
old = '''        # Position size in quote-notional terms (simplified), bounded by balance*leverage
        position_notional = min((risk_amount / stop_dist), balance * lev)
        qty = position_notional / entry
'''
new = '''        # Position size in quote-notional terms. Hard user-approved per-position
        # ceilings apply to notional exposure, not margin or leverage.
        hard_position_cap = 80.0 if market == MarketType.SPOT else 800.0 if market == MarketType.FUTURES else 0.0
        if hard_position_cap <= 0:
            issues.append("unsupported_market_for_trade_sizing")
            return {"ok": False, "issues": issues, "position_size": 0.0}
        requested_notional = min((risk_amount / stop_dist), balance * lev)
        position_notional = min(requested_notional, hard_position_cap)
        if requested_notional > hard_position_cap:
            issues.append(f"per_position_notional_capped:{hard_position_cap}")
        qty = position_notional / entry
'''
if new not in s:
    if old not in s:
        raise SystemExit("RiskEngine sizing block missing")
    s = s.replace(old, new, 1)
p.write_text(s)

TESTS = r'''from app.core.enums import EnvironmentMode, MarketType
from app.engines.risk import RiskEngine


def test_spot_risk_sizing_never_exceeds_80_usdt_notional():
    result = RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO, market=MarketType.SPOT,
        balance=10000, risk_per_trade=0.01, entry=100, stop_loss=99.9,
        leverage=1, max_leverage=10,
    )
    assert result["ok"] is True
    assert result["position_notional_estimate"] <= 80.0
    assert "per_position_notional_capped:80.0" in result["issues"]


def test_futures_risk_sizing_never_exceeds_800_usdt_notional():
    result = RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO, market=MarketType.FUTURES,
        balance=10000, risk_per_trade=0.01, entry=100, stop_loss=99.9,
        leverage=10, max_leverage=10,
    )
    assert result["ok"] is True
    assert result["position_notional_estimate"] <= 800.0
    assert "per_position_notional_capped:800.0" in result["issues"]


def test_risk_engine_does_not_apply_leverage_to_raise_notional_ceiling():
    result = RiskEngine().validate_trade(
        environment=EnvironmentMode.DEMO, market=MarketType.FUTURES,
        balance=10000, risk_per_trade=0.01, entry=100, stop_loss=99.9,
        leverage=20, max_leverage=20,
    )
    assert result["position_notional_estimate"] <= 800.0
'''
(root / "tests/test_risk_position_caps.py").write_text(TESTS)
