from pathlib import Path
import sys

root = Path(sys.argv[1])

service = r'''"""Portfolio exposure and concentration reporting.

Reports only persisted shadow positions. It does not invent exchange balances,
place orders, rebalance capital, or treat simulated values as live account data.
"""
from __future__ import annotations

from collections import defaultdict
from math import isfinite
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import PositionRecord


LONG_SIDES = {"long", "long_spot", "buy"}
SHORT_SIDES = {"short", "sell"}


def _number(value: Any) -> float:
    try:
        result = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return result if isfinite(result) else 0.0


class PortfolioManagementService:
    def build_report(
        self, db: Session, *, environment_mode: str = "demo",
        market_type: str | None = None, include_closed: bool = False,
    ) -> dict[str, Any]:
        query = select(PositionRecord).where(
            PositionRecord.environment_mode == environment_mode,
            PositionRecord.shadow.is_(True),
        )
        if not include_closed:
            query = query.where(PositionRecord.status == "open")
        if market_type:
            query = query.where(PositionRecord.market_type == market_type)
        rows = db.execute(query.order_by(PositionRecord.id)).scalars().all()

        positions: list[dict[str, Any]] = []
        by_market: dict[str, dict[str, float]] = defaultdict(
            lambda: {"positions": 0, "gross_notional": 0.0, "net_notional": 0.0,
                     "unrealized_pnl": 0.0, "realized_pnl": 0.0, "fees": 0.0}
        )
        by_symbol: dict[str, dict[str, float]] = defaultdict(
            lambda: {"positions": 0, "gross_notional": 0.0, "net_notional": 0.0,
                     "unrealized_pnl": 0.0}
        )
        total_gross = total_net = total_unrealized = total_realized = total_fees = 0.0
        violations: list[dict[str, Any]] = []

        for row in rows:
            qty = abs(_number(row.quantity))
            mark = _number(row.mark_price) or _number(row.entry_avg_price)
            entry = _number(row.entry_avg_price)
            notional = qty * mark
            side = str(row.side or "").lower()
            signed = notional if side in LONG_SIDES else -notional if side in SHORT_SIDES else 0.0
            market = str(row.market_type or "").lower()
            leverage = max(1.0, _number(row.leverage))
            unrealized = _number(row.unrealized_pnl)
            realized = _number(row.realized_pnl)
            fees = _number(row.fees_total)
            item = {
                "position_id": row.id, "symbol": row.symbol, "market_type": market,
                "side": side, "status": row.status, "quantity": qty,
                "entry_price": entry or None, "mark_price": mark or None,
                "notional_exposure": round(notional, 8),
                "signed_exposure": round(signed, 8), "leverage": leverage,
                "unrealized_pnl": round(unrealized, 8),
                "realized_pnl": round(realized, 8), "fees": round(fees, 8),
                "shadow": True,
            }
            positions.append(item)
            group = by_market[market]
            group["positions"] += 1
            group["gross_notional"] += notional
            group["net_notional"] += signed
            group["unrealized_pnl"] += unrealized
            group["realized_pnl"] += realized
            group["fees"] += fees
            symbol_group = by_symbol[row.symbol]
            symbol_group["positions"] += 1
            symbol_group["gross_notional"] += notional
            symbol_group["net_notional"] += signed
            symbol_group["unrealized_pnl"] += unrealized
            total_gross += notional
            total_net += signed
            total_unrealized += unrealized
            total_realized += realized
            total_fees += fees

            if row.status == "open" and market == "spot" and notional > 80.0 + 1e-8:
                violations.append({"position_id": row.id, "symbol": row.symbol,
                                   "rule": "spot_position_notional_cap_80_usdt",
                                   "observed": round(notional, 8), "limit": 80.0})
            if row.status == "open" and market == "futures" and notional > 800.0 + 1e-8:
                violations.append({"position_id": row.id, "symbol": row.symbol,
                                   "rule": "futures_position_notional_cap_800_usdt",
                                   "observed": round(notional, 8), "limit": 800.0})
            if row.status == "open" and market == "futures" and leverage > settings.max_leverage_cap + 1e-8:
                violations.append({"position_id": row.id, "symbol": row.symbol,
                                   "rule": "futures_leverage_cap",
                                   "observed": leverage, "limit": settings.max_leverage_cap})

        def rounded_groups(groups: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
            result = {}
            for key, values in groups.items():
                result[key] = {
                    k: (int(v) if k == "positions" else round(v, 8))
                    for k, v in values.items()
                }
            return result

        return {
            "environment_mode": environment_mode,
            "market_filter": market_type,
            "scope": "persisted_shadow_positions_only",
            "positions_count": len(positions),
            "open_positions_count": sum(1 for p in positions if p["status"] == "open"),
            "gross_notional_exposure": round(total_gross, 8),
            "net_directional_exposure": round(total_net, 8),
            "unrealized_pnl": round(total_unrealized, 8),
            "realized_pnl": round(total_realized, 8),
            "fees_recorded": round(total_fees, 8),
            "by_market": rounded_groups(by_market),
            "by_symbol": rounded_groups(by_symbol),
            "positions": positions,
            "risk_limit_violations": violations,
            "exchange_balance": None,
            "equity": None,
            "notes": [
                "This report contains shadow records only; no live/testnet exchange account balance is queried.",
                "Notional exposure is absolute quantity multiplied by mark price; leverage does not multiply notional.",
                "No automatic rebalancing or order placement is performed.",
                "Missing mark prices fall back to entry price and are identified by the source fields.",
            ],
        }


portfolio_management = PortfolioManagementService()
'''

(root / "app/services/portfolio_management.py").write_text(service)

core_path = root / "app/services/core_service.py"
core = core_path.read_text()
if "from .portfolio_management import portfolio_management" not in core:
    core = core.replace(
        "from .position_management import position_management\n",
        "from .position_management import position_management\nfrom .portfolio_management import portfolio_management\n",
        1,
    )
core = core.replace(
    '("portfolio_management", "Portfolio Management Engine", "not_implemented"),',
    '("portfolio_management", "Portfolio Management Engine", "implemented_partial"),',
)
core_path.write_text(core)

routes_path = root / "app/api/routes.py"
routes = routes_path.read_text()
if "from app.services.portfolio_management import portfolio_management" not in routes:
    routes = routes.replace(
        "from app.services.position_management import position_management\n",
        "from app.services.position_management import position_management\nfrom app.services.portfolio_management import portfolio_management\n",
        1,
    )
if '@router.get("/portfolio/report")' not in routes:
    marker = """@router.get("/positions/protection")
def list_position_protections(
"""
    addition = """@router.get("/portfolio/report")
def portfolio_report(
    environment_mode: str = "demo",
    market_type: str | None = None,
    include_closed: bool = False,
    db: Session = Depends(get_db), _user=Depends(get_current_user),
) -> dict[str, Any]:
    if environment_mode not in {"demo", "live"}:
        raise HTTPException(status_code=400, detail="invalid_environment_mode")
    if market_type is not None and market_type not in {"spot", "futures"}:
        raise HTTPException(status_code=400, detail="invalid_market_type")
    return portfolio_management.build_report(
        db, environment_mode=environment_mode, market_type=market_type,
        include_closed=include_closed,
    )


"""
    if marker not in routes:
        raise SystemExit("Portfolio API route insertion marker missing.")
    routes = routes.replace(marker, addition + marker, 1)
routes_path.write_text(routes)

tests = r'''from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.db import Base
from app.models.entities import PositionRecord
from app.services.portfolio_management import PortfolioManagementService


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'portfolio-management.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _add(db, *, symbol, market, side, qty, mark, leverage=1, shadow=True, status="open"):
    row = PositionRecord(
        environment_mode="demo", market_type=market, symbol=symbol, side=side,
        quantity=qty, entry_avg_price=mark, mark_price=mark, leverage=leverage,
        unrealized_pnl=2.5, realized_pnl=1.0, fees_total=0.2,
        status=status, shadow=shadow, metadata_json={},
    )
    db.add(row)
    db.flush()
    return row


def test_portfolio_report_groups_spot_and_futures_and_uses_notional_not_margin(tmp_path):
    db = _db(tmp_path)
    try:
        _add(db, symbol="BTC/USDT", market="spot", side="long_spot", qty=0.001, mark=60000)
        _add(db, symbol="ETH/USDT", market="futures", side="short", qty=0.1, mark=3000, leverage=5)
        result = PortfolioManagementService().build_report(db)
        assert result["positions_count"] == 2
        assert result["gross_notional_exposure"] == 360.0
        assert result["net_directional_exposure"] == -240.0
        assert result["by_market"]["spot"]["gross_notional"] == 60.0
        assert result["by_market"]["futures"]["gross_notional"] == 300.0
        assert result["exchange_balance"] is None and result["equity"] is None
    finally:
        db.close()


def test_portfolio_report_flags_existing_spot_and_futures_notional_cap_breaches(tmp_path):
    db = _db(tmp_path)
    try:
        _add(db, symbol="BTC/USDT", market="spot", side="long_spot", qty=0.002, mark=50000)
        _add(db, symbol="ETH/USDT", market="futures", side="long", qty=0.3, mark=3000, leverage=2)
        result = PortfolioManagementService().build_report(db)
        rules = {item["rule"] for item in result["risk_limit_violations"]}
        assert "spot_position_notional_cap_80_usdt" in rules
        assert "futures_position_notional_cap_800_usdt" in rules
    finally:
        db.close()


def test_portfolio_report_excludes_real_positions_and_filters_market(tmp_path):
    db = _db(tmp_path)
    try:
        _add(db, symbol="BTC/USDT", market="spot", side="long_spot", qty=0.001, mark=50000)
        _add(db, symbol="ETH/USDT", market="futures", side="long", qty=0.1, mark=3000)
        _add(db, symbol="XRP/USDT", market="spot", side="long_spot", qty=1, mark=1, shadow=False)
        result = PortfolioManagementService().build_report(db, market_type="futures")
        assert result["positions_count"] == 1
        assert result["positions"][0]["symbol"] == "ETH/USDT"
        assert result["scope"] == "persisted_shadow_positions_only"
    finally:
        db.close()


def test_closed_positions_are_excluded_unless_requested(tmp_path):
    db = _db(tmp_path)
    try:
        _add(db, symbol="BTC/USDT", market="spot", side="long_spot", qty=0.001, mark=50000, status="closed")
        service = PortfolioManagementService()
        assert service.build_report(db)["positions_count"] == 0
        assert service.build_report(db, include_closed=True)["positions_count"] == 1
    finally:
        db.close()
'''
(root / "tests/test_portfolio_management.py").write_text(tests)
