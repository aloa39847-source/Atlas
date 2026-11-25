from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.enums import Timeframe
from app.engines.market_data import Candles
from app.services.core_service import TitanCoreService


def _batches(age_seconds=0.0, source="live_public"):
    now_ms = int((datetime.now(timezone.utc).timestamp() - age_seconds) * 1000)
    result = {}
    for timeframe in (Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5):
        rows = []
        for index in range(30):
            stamp = now_ms - (29 - index) * 60_000
            rows.append({
                "timestamp": stamp, "close_time": stamp + 59_999,
                "open": 100.0, "high": 101.0, "low": 99.0,
                "close": 100.0, "volume": 100.0,
            })
        result[timeframe] = SimpleNamespace(
            source=SimpleNamespace(value=source),
            df=Candles(rows),
        )
    return result


def test_public_market_data_requires_all_five_verified_timeframes():
    TitanCoreService._validate_public_market_batches(_batches())
    missing = _batches()
    missing.pop(Timeframe.M5)
    with pytest.raises(ValueError, match="verified_binance_spot_public_data_required:5m"):
        TitanCoreService._validate_public_market_batches(missing)


def test_public_market_data_rejects_stale_and_future_candles():
    with pytest.raises(ValueError, match="stale_or_future_public_market_data:5m"):
        TitanCoreService._validate_public_market_batches(_batches(age_seconds=1200))

    future = _batches()
    future[Timeframe.M5].df.rows[-1]["close_time"] = int(
        (datetime.now(timezone.utc).timestamp() + 120) * 1000
    )
    with pytest.raises(ValueError, match="stale_or_future_public_market_data:5m"):
        TitanCoreService._validate_public_market_batches(future)


def test_public_market_data_rejects_synthetic_source_and_bad_timestamps():
    with pytest.raises(ValueError, match="verified_binance_spot_public_data_required:1d"):
        TitanCoreService._validate_public_market_batches(_batches(source="synthetic_labeled"))

    invalid = _batches()
    invalid[Timeframe.M5].df.rows[-1]["close_time"] = float("nan")
    with pytest.raises(ValueError, match="stale_or_future_public_market_data:5m"):
        TitanCoreService._validate_public_market_batches(invalid)


def test_public_market_data_must_match_requested_futures_market():
    futures = _batches()
    for batch in futures.values():
        batch.market_type = "futures"
    TitanCoreService._validate_public_market_batches(futures, expected_market="futures")

    with pytest.raises(ValueError, match="verified_binance_futures_public_data_required:1d"):
        TitanCoreService._validate_public_market_batches(_batches(), expected_market="futures")
