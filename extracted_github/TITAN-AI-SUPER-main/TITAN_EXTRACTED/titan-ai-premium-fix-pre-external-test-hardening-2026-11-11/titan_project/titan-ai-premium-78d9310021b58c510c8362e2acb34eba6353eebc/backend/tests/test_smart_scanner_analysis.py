from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.enums import DataSourceKind, Timeframe
from app.engines.market_data import Candles
from app.services.core_service import core_service
from app.services.smart_scanner import SmartScannerService


def _batches(*, stale_timeframe=None):
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    batches = {}
    for timeframe in (Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5):
        stamp = now_ms - (10_000_000 if timeframe == stale_timeframe else 10_000)
        rows = [{
            "timestamp": stamp - 300_000,
            "close_time": stamp,
            "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.5, "volume": 1000.0,
        } for _ in range(40)]
        batches[timeframe] = SimpleNamespace(
            df=Candles(rows), source=DataSourceKind.LIVE_PUBLIC, note="public test candles"
        )
    return batches


def test_smart_scanner_analyzes_ranked_spot_candidates_with_existing_decision_engine(monkeypatch):
    monkeypatch.setattr(core_service.market_data, "scan_spot_universe", lambda **kwargs: {
        "ok": True, "available_candidates": 2, "items": [
            {"symbol": "AAA/USDT", "quote_volume_usdt_24h": 5_000_000},
            {"symbol": "BBB/USDT", "quote_volume_usdt_24h": 4_000_000},
        ],
    })
    monkeypatch.setattr(core_service.market_data, "get_public_multi_timeframe", lambda symbol: _batches())
    analyzed = []

    def fake_analyze(db, **kwargs):
        analyzed.append(kwargs["symbol"])
        return {
            "action": "buy" if kwargs["symbol"] == "BBB/USDT" else "wait",
            "confidence": 0.62, "reason": "test decision", "strategy": "test",
            "timeframes": {"1d": {"trend": "bullish"}},
            "decision_id": len(analyzed), "data_source": "binance_public",
        }

    monkeypatch.setattr(core_service, "analyze", fake_analyze)
    result = SmartScannerService().scan(object(), max_candidates=2)

    assert result["ok"] is True
    assert result["source"] == "binance_public_read_only"
    assert result["execution"] == "analysis_only_no_shadow_or_exchange_orders"
    assert analyzed == ["AAA/USDT", "BBB/USDT"]
    assert [item["symbol"] for item in result["items"]] == ["BBB/USDT", "AAA/USDT"]
    assert result["items"][0]["action"] == "buy"
    assert set(result["items"][0]["coverage"]) == {"1d", "4h", "1h", "15m", "5m"}
    assert result["items"][0]["execution"] == "not_performed"


def test_smart_scanner_skips_stale_candidate_without_running_decision(monkeypatch):
    monkeypatch.setattr(core_service.market_data, "scan_spot_universe", lambda **kwargs: {
        "ok": True, "available_candidates": 2, "items": [
            {"symbol": "OLD/USDT", "quote_volume_usdt_24h": 5_000_000},
            {"symbol": "FRESH/USDT", "quote_volume_usdt_24h": 4_000_000},
        ],
    })
    monkeypatch.setattr(
        core_service.market_data, "get_public_multi_timeframe",
        lambda symbol: _batches(stale_timeframe=Timeframe.M5) if symbol == "OLD/USDT" else _batches(),
    )
    analyzed = []
    monkeypatch.setattr(core_service, "analyze", lambda db, **kwargs: (
        analyzed.append(kwargs["symbol"]) or {"action": "wait", "confidence": 0.2, "reason": "wait"}
    ))

    result = SmartScannerService().scan(object(), max_candidates=2)

    assert analyzed == ["FRESH/USDT"]
    assert result["analyzed"] == 1
    assert result["skipped"] == 1
    assert result["skipped_items"][0]["symbol"] == "OLD/USDT"
    assert "stale_or_future_candles:5m" in result["skipped_items"][0]["reason"]


@pytest.mark.parametrize("kwargs", [
    {"max_candidates": 0},
    {"max_candidates": 21},
    {"min_quote_volume_usdt": -1},
    {"balance": 0},
    {"risk_per_trade": 0.01},
])
def test_smart_scanner_rejects_unsafe_or_unbounded_parameters(kwargs):
    with pytest.raises(ValueError):
        SmartScannerService().scan(object(), **kwargs)


@pytest.mark.parametrize("kwargs", [
    {"balance": True},
    {"risk_per_trade": True},
    {"min_quote_volume_usdt": True},
    {"max_data_age_seconds": True},
    {"balance": "not-a-number"},
])
def test_smart_scanner_rejects_boolean_and_malformed_numeric_inputs(kwargs):
    with pytest.raises(ValueError):
        SmartScannerService().scan(object(), **kwargs)
