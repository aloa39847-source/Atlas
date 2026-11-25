from pathlib import Path
import sys

root = Path(sys.argv[1])

def replace_file(rel, old, new, label):
    path = root / rel
    content = path.read_text()
    if new in content:
        return
    if old not in content:
        raise SystemExit(f"{label}: expected source marker missing; refusing patch")
    path.write_text(content.replace(old, new, 1))

replace_file(
    "app/core/config.py",
    '    binance_api_key: str = field(default_factory=lambda: os.getenv("BINANCE_API_KEY", ""))\n',
    '    binance_public_data_enabled: bool = field(default_factory=lambda: _bool("TITAN_BINANCE_PUBLIC_DATA_ENABLED", False))\n'
    '    binance_api_key: str = field(default_factory=lambda: os.getenv("BINANCE_API_KEY", ""))\n',
    "public data setting",
)

public_adapter = r'''"""Read-only Binance public market-data adapter for Spot and USD-M Futures."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any


class BinancePublicData:
    SPOT_BASE = "https://api.binance.com"
    FUTURES_BASE = "https://fapi.binance.com"
    ALLOWED_MARKETS = {"spot", "futures"}
    ALLOWED_INTERVALS = {"1d", "4h", "1h", "15m", "5m"}

    def get_klines(self, symbol: str, interval: str, limit: int = 500, market: str = "spot") -> list[list[Any]]:
        market = (market or "spot").lower()
        if market not in self.ALLOWED_MARKETS:
            raise ValueError("unsupported_market_for_public_klines")
        if interval not in self.ALLOWED_INTERVALS:
            raise ValueError("unsupported_kline_interval")
        limit = max(30, min(int(limit), 1000))
        base = self.FUTURES_BASE if market == "futures" else self.SPOT_BASE
        path = "/fapi/v1/klines" if market == "futures" else "/api/v3/klines"
        query = urllib.parse.urlencode({"symbol": symbol.replace("/", "").upper(), "interval": interval, "limit": limit})
        url = f"{base}{path}?{query}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TITAN-AI-PREMIUM/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            if not isinstance(data, list):
                raise RuntimeError("unexpected_kline_response")
            return data
        except Exception as e:
            raise RuntimeError(f"binance_public_error:{e}") from e

    def klines_to_candles(self, klines: list[list[Any]]) -> list[dict]:
        candles = []
        for k in klines:
            if not isinstance(k, (list, tuple)) or len(k) < 7:
                continue
            candles.append({
                "timestamp": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": int(k[6]),
                "source": "binance_public",
            })
        return candles
'''
(root / "app/data/binance_public.py").write_text(public_adapter)

market_path = root / "app/engines/market_data.py"
market = market_path.read_text()
market = market.replace(
    "Current implementation provides LABELED synthetic candles for development/testing.\n    Live/public/historical Binance adapters are integration points (needs external service/keys/config).",
    "Labeled synthetic candles remain the default. Optional read-only Binance Spot/Futures public candles are enabled with TITAN_BINANCE_PUBLIC_DATA_ENABLED=true."
)
if "self.use_live_public_data = settings.binance_public_data_enabled" not in market:
    market = market.replace("import random\n", "import random\nimport time\n", 1)
    market = market.replace(
        "from app.core.enums import DataSourceKind, EngineStatus, Timeframe\n",
        "from app.core.enums import DataSourceKind, EngineStatus, Timeframe\nfrom app.core.config import settings\nfrom app.data.binance_public import BinancePublicData\n",
        1,
    )
    old = '''    def get_multi_timeframe(self, symbol: str, limit: int = 120, seed: int | None = 42) -> dict[Timeframe, CandleBatch]:
        return {tf: self.get_synthetic_candles(symbol, tf, limit=limit, seed=(seed + i) if seed is not None else None)
                for i, tf in enumerate([Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5])}
'''
    new = '''    def __init__(self) -> None:
        self.public_data = BinancePublicData()
        self.use_live_public_data = settings.binance_public_data_enabled

    def get_multi_timeframe(self, symbol: str, limit: int = 120, seed: int | None = 42,
                            market_type: str = "spot") -> dict[Timeframe, CandleBatch]:
        timeframes = [Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5]
        if not self.use_live_public_data:
            return {tf: self.get_synthetic_candles(symbol, tf, limit=limit, seed=(seed + i) if seed is not None else None)
                    for i, tf in enumerate(timeframes)}
        market = (market_type or "spot").lower()
        if market not in {"spot", "futures"}:
            raise ValueError("live_public_data_supports_spot_or_futures_only")
        now_ms = int(time.time() * 1000)
        tf_minutes = {Timeframe.D1: 1440, Timeframe.H4: 240, Timeframe.H1: 60, Timeframe.M15: 15, Timeframe.M5: 5}
        batches = {}
        for tf in timeframes:
            raw = self.public_data.get_klines(symbol, tf.value, limit=limit, market=market)
            candles = self.public_data.klines_to_candles(raw)
            closed = [c for c in candles if c.get("close_time", now_ms) < now_ms]
            if len(closed) < 30:
                raise RuntimeError(f"insufficient_closed_live_candles:{market}:{symbol}:{tf.value}")
            latest_age = now_ms - closed[-1]["close_time"]
            if latest_age > tf_minutes[tf] * 2 * 60_000:
                raise RuntimeError(f"stale_live_market_data:{market}:{symbol}:{tf.value}")
            rows = [{k: c[k] for k in ("timestamp", "open", "high", "low", "close", "volume")} for c in closed]
            batches[tf] = CandleBatch(
                symbol=symbol, timeframe=tf, df=Candles(rows), source=DataSourceKind.LIVE_PUBLIC,
                note=f"Live Binance public {market} candles; read-only, closed candles only; no API key required.",
            )
        return batches
'''
    if old not in market:
        raise SystemExit("MarketDataEngine multi-timeframe marker missing; refusing public data patch")
    market = market.replace(old, new, 1)
    market_path.write_text(market)

replace_file(
    "app/services/core_service.py",
    "batches = self.market_data.get_multi_timeframe(symbol, seed=42)",
    "batches = self.market_data.get_multi_timeframe(symbol, seed=42, market_type=market.value)",
    "core service market selection",
)
replace_file(
    "app/services/core_service.py",
    'data_source="synthetic_labeled",',
    'data_source=(next(iter({b.source.value for b in batches.values()})) if len({b.source.value for b in batches.values()}) == 1 else "mixed"),',
    "decision provenance persistence",
)
replace_file(
    "app/services/core_service.py",
    '"note": "Synthetic-labeled run; replay requires same provider/config/seed semantics."',
    '"note": "Replay requires the same data provider/configuration and candle snapshot; source labels are recorded above."',
    "analysis replay provenance note",
)
replace_file(
    "app/services/orchestrator.py",
    "core_service.market_data.get_multi_timeframe(symbol, seed=42)",
    "core_service.market_data.get_multi_timeframe(symbol, seed=42, market_type=market)",
    "orchestrator market selection",
)

decision_path = root / "app/engines/decision.py"
decision = decision_path.read_text()
decision = decision.replace(
    "from app.core.enums import DecisionAction, EngineStatus, EnvironmentMode, MarketType",
    "from app.core.enums import DataSourceKind, DecisionAction, EngineStatus, EnvironmentMode, MarketType, Timeframe",
    1,
)
old = '''            "data_sources": {k: "synthetic_labeled" for k in tf.keys()},
            "data_source_warning": "Decision uses LABELED synthetic data in this build unless a real data adapter is configured and verified.",
'''
new = '''            "data_sources": {k: (batches[Timeframe(k)].source.value if batches and Timeframe(k) in batches else "unknown") for k in tf.keys()},
            "data_source_warning": ("Live read-only Binance public candles used; no account credentials or order access." if batches and all(b.source == DataSourceKind.LIVE_PUBLIC for b in batches.values()) else "Decision uses LABELED synthetic data; it is not a real-market signal." if batches and all(b.source == DataSourceKind.SYNTHETIC_LABELED for b in batches.values()) else "Data provenance unavailable; do not use this decision for execution."),
'''
if new not in decision:
    if old not in decision:
        raise SystemExit("Decision data provenance marker missing; refusing patch")
    decision = decision.replace(old, new, 1)
decision_path.write_text(decision)

replace_file(
    "app/api/routes.py",
    '"data_policy": {"live_market_data": False, "synthetic_labeled_available": True, "note": "UI/API must never present synthetic data as real market data."},',
    '"data_policy": {"live_market_data": settings.binance_public_data_enabled, "synthetic_labeled_available": True, "note": "Live public data is read-only; synthetic data must never be presented as real market data."},',
    "API live data status",
)
replace_file(
    "app/star_app.py",
    '"data_policy": {"live_market_data": False, "synthetic_labeled_available": True, "binance_public_available": True, "note": "Distinguish clearly: public read-only vs testnet demo vs live (disabled)."},',
    '"data_policy": {"live_market_data": settings.binance_public_data_enabled, "synthetic_labeled_available": True, "binance_public_available": True, "note": "Distinguish clearly: public read-only vs testnet demo vs live (disabled)."},',
    "Starlette live data status",
)

foundation = root / "tests/test_foundation.py"
foundation_text = foundation.read_text()
old_call = 'result = de.decide(symbol="BTC/USDT", market=MarketType.SPOT, environment=EnvironmentMode.DEMO, timeframe_analyses=analyses)'
new_call = 'result = de.decide(symbol="BTC/USDT", market=MarketType.SPOT, environment=EnvironmentMode.DEMO, timeframe_analyses=analyses, batches=batches)'
if new_call not in foundation_text:
    if old_call not in foundation_text:
        raise SystemExit("Foundation provenance test marker missing")
    foundation.write_text(foundation_text.replace(old_call, new_call, 1))

LIVE_TEST = r'''import time
import pytest
from app.core.config import settings
from app.core.enums import DataSourceKind, Timeframe
from app.engines.market_data import MarketDataEngine


class FakeBinancePublic:
    def __init__(self, fail=False):
        self.fail = fail
        self.markets = []

    def get_klines(self, symbol, interval, limit=500, market="spot"):
        self.markets.append((symbol, interval, market))
        if self.fail:
            raise RuntimeError("feed_down")
        now = int(time.time() * 1000)
        step = {"1d": 1440, "4h": 240, "1h": 60, "15m": 15, "5m": 5}[interval] * 60_000
        return [[now - (120 - i) * step - step, "100", "101", "99", "100.5", "10", now - (120 - i) * step - 60_000] for i in range(120)]

    def klines_to_candles(self, klines):
        return [{"timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]), "close_time": int(k[6])} for k in klines]


def test_live_public_market_data_uses_all_five_closed_timeframes(monkeypatch):
    monkeypatch.setattr(settings, "binance_public_data_enabled", True)
    md = MarketDataEngine()
    fake = FakeBinancePublic()
    md.public_data = fake
    batches = md.get_multi_timeframe("BTC/USDT", market_type="futures")
    assert set(batches) == {Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5}
    assert all(b.source == DataSourceKind.LIVE_PUBLIC for b in batches.values())
    assert all("read-only" in b.note for b in batches.values())
    assert all(m == "futures" for _, _, m in fake.markets)


def test_live_data_failure_does_not_fall_back_to_synthetic(monkeypatch):
    monkeypatch.setattr(settings, "binance_public_data_enabled", True)
    md = MarketDataEngine()
    md.public_data = FakeBinancePublic(fail=True)
    with pytest.raises(RuntimeError, match="feed_down"):
        md.get_multi_timeframe("BTC/USDT", market_type="spot")


def test_synthetic_data_remains_default(monkeypatch):
    monkeypatch.setattr(settings, "binance_public_data_enabled", False)
    md = MarketDataEngine()
    batches = md.get_multi_timeframe("BTC/USDT")
    assert all(b.source == DataSourceKind.SYNTHETIC_LABELED for b in batches.values())
'''
(root / "tests/test_live_public_market_data.py").write_text(LIVE_TEST)
