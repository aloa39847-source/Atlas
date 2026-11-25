from datetime import datetime, timezone

from app.engines.technical import _vwap


def test_vwap_resets_at_utc_session_boundary():
    high = [11, 21, 31]
    low = [9, 19, 29]
    close = [10, 20, 30]
    volume = [1, 1, 1]
    timestamps = [
        "2026-11-09T23:55:00Z",
        "2026-11-09T23:59:00Z",
        "2026-11-10T00:00:00Z",
    ]
    values = _vwap(high, low, close, volume, timestamps)
    assert values[0] == 10
    assert values[1] == 15
    assert values[2] == 30


def test_vwap_accepts_binance_millisecond_and_epoch_second_timestamps():
    values_ms = _vwap(
        [11, 21], [9, 19], [10, 20], [1, 1],
        [1794268500000, 1794268800000],
    )
    values_s = _vwap(
        [11, 21], [9, 19], [10, 20], [1, 1],
        [1794268500, 1794268800],
    )
    assert values_ms == values_s


def test_vwap_accepts_datetime_and_normalizes_timezone_to_utc():
    values = _vwap(
        [11, 21], [9, 19], [10, 20], [1, 1],
        [
            datetime(2026, 11, 9, 23, 55, tzinfo=timezone.utc),
            datetime(2026, 11, 10, 1, 0, tzinfo=timezone.utc),
        ],
    )
    assert values == [10, 20]


def test_vwap_without_timestamps_uses_one_batch_anchor():
    values = _vwap([11, 21], [9, 19], [10, 20], [1, 1])
    assert values == [10, 15]


def test_vwap_zero_volume_returns_none_until_volume_is_positive():
    values = _vwap([11, 21], [9, 19], [10, 20], [0, 2])
    assert values == [None, 20]
