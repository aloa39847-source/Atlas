from pathlib import Path
import sys

root = Path(sys.argv[1])
p = root / "app/services/shadow_trading.py"
s = p.read_text()
if "all_positions = db.execute(select(PositionRecord)" not in s:
    start = s.index("    def _snapshot(")
    end = s.index("\n    def create_shadow_order(", start)
    new = r"""    def _snapshot(self, db: Session, *, env: str, market: str, cash: float = 10000.0) -> PortfolioSnapshot:
        all_positions = db.execute(select(PositionRecord).where(
            PositionRecord.environment_mode == env, PositionRecord.market_type == market
        )).scalars().all()
        open_positions = [p for p in all_positions if p.status == "open" and p.quantity > 0]
        orders = db.execute(select(OrderRecord).where(
            OrderRecord.environment_mode == env, OrderRecord.market_type == market
        )).scalars().all()
        exposure = sum(abs((p.mark_price or p.entry_avg_price or 0) * p.quantity) for p in open_positions)
        unreal = sum(p.unrealized_pnl for p in open_positions)
        # Position records carry aggregate realized gross PnL and all entry/exit fees.
        # Do not add OrderRecord.fees_paid again: entry fees are already included.
        realized = sum(p.realized_pnl for p in all_positions)
        fees = sum(p.fees_total for p in all_positions)
        snap = PortfolioSnapshot(environment_mode=env, market_type=market, cash_balance=cash, equity_estimate=cash + unreal + realized - fees,
                                 unrealized_pnl=unreal, realized_pnl=realized, fees_total=fees, exposure_notional=exposure,
                                 positions_open=len(open_positions), details={"order_count": len(orders)})
        db.add(snap); db.flush()
        return snap
"""
    s = s[:start] + new + s[end:]
if "realized_gross =" not in s:
    old = '''        realized = round(direction * (close_price - (pos.entry_avg_price or close_price)) * pos.quantity - fee, 8)
        pos.realized_pnl += realized
'''
    new = '''        realized_gross = round(direction * (close_price - (pos.entry_avg_price or close_price)) * pos.quantity, 8)
        realized_net = round(realized_gross - fee, 8)
        pos.realized_pnl += realized_gross
'''
    if old not in s:
        raise SystemExit("Shadow close realized-PnL marker missing.")
    s = s.replace(old, new, 1)
    old = '''                  details={"close_price": close_price, "realized_pnl": realized, "fee": fee, "snapshot_id": snap.id})
        return {"ok": True, "position_id": pos.id, "realized_pnl": realized, "fee": fee, "snapshot_id": snap.id}
'''
    new = '''                  details={"close_price": close_price, "realized_pnl_gross": realized_gross, "realized_pnl_net": realized_net,
                           "fee": fee, "snapshot_id": snap.id})
        return {"ok": True, "position_id": pos.id, "realized_pnl": realized_net,
                "realized_pnl_gross": realized_gross, "fee": fee, "snapshot_id": snap.id}
'''
    if old not in s:
        raise SystemExit("Shadow close return/audit marker missing.")
    s = s.replace(old, new, 1)
p.write_text(s)

p = root / "tests/test_shadow_lifecycle.py"
s = p.read_text()
if "test_snapshot_counts_fees_once_and_retains_closed_realized_pnl" not in s:
    s += '''

def test_snapshot_counts_fees_once_and_retains_closed_realized_pnl(tmp_path):
    db = _db(tmp_path)
    try:
        svc = ShadowTradingService()
        opened = svc.create_shadow_order(
            db, env="demo", market="spot", symbol="BTC/USDT", side="buy", order_type="limit",
            quantity=0.8, price=100.0, reference_price=100.0,
            idempotency_key="shadow-accounting-open-01", simulate="fill", fee_rate=0.001,
        )
        db.commit()
        assert opened.ok and opened.position is not None
        before = svc.get_portfolio(db, env="demo", market="spot")
        assert abs(before["snapshot"]["fees_total"] - 0.08) < 1e-8

        closed = svc.close_shadow_position(db, position_id=opened.position.id, close_price=110.0, fee_rate=0.001)
        db.commit()
        assert closed["ok"] is True
        assert abs(closed["realized_pnl_gross"] - 8.0) < 1e-8
        assert abs(closed["realized_pnl"] - 7.912) < 1e-8
        after = svc.get_portfolio(db, env="demo", market="spot")
        assert after["snapshot"]["positions_open"] == 0
        assert abs(after["snapshot"]["realized_pnl"] - 8.0) < 1e-8
        assert abs(after["snapshot"]["fees_total"] - 0.168) < 1e-8
        assert abs(after["snapshot"]["equity_estimate"] - 10007.832) < 1e-8
    finally:
        db.close()
'''
p.write_text(s)
