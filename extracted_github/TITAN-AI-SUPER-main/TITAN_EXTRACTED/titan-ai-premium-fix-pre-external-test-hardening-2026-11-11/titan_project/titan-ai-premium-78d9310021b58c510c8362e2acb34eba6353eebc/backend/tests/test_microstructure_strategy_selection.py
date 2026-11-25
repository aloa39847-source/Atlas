from datetime import datetime, timedelta, timezone

from app.core.enums import MarketType
from app.engines.strategies import select_strategy


def _evidence(*, depth=0.3, flow=0.25, spread=0.05, liquidity_source="binance_public_read_only",
              flow_source="binance_public_read_only", liquidity_symbol="BTC/USDT",
              flow_symbol="BTC/USDT", book_age_seconds=0, first_trade_age_seconds=10,
              last_trade_age_seconds=0):
    now = datetime.now(timezone.utc)
    return {
        "liquidity": {
            "ok": True, "source": liquidity_source, "symbol": liquidity_symbol,
            "fetched_at": (now - timedelta(seconds=book_age_seconds)).isoformat(),
            "spread_percent": spread, "depth_imbalance": depth,
        },
        "order_flow": {
            "ok": True, "source": flow_source, "symbol": flow_symbol,
            "first_trade_time": (now - timedelta(seconds=first_trade_age_seconds)).isoformat(),
            "last_trade_time": (now - timedelta(seconds=last_trade_age_seconds)).isoformat(),
            "taker_flow_imbalance": flow,
        },
    }


def test_microstructure_strategy_requires_agreeing_bullish_public_evidence():
    result = select_strategy(
        MarketType.SPOT, "bullish", [], microstructure_analysis=_evidence(),
    )
    assert result["key"] == "liquidity_orderflow"
    assert result["status"] == "implemented_partial"


def test_microstructure_strategy_requires_agreeing_bearish_public_evidence():
    result = select_strategy(
        MarketType.FUTURES, "bearish", [],
        microstructure_analysis=_evidence(
            depth=-0.3, flow=-0.25,
            liquidity_source="binance_futures_public_read_only",
            flow_source="binance_futures_public_read_only",
        ),
    )
    assert result["key"] == "liquidity_orderflow"


def test_microstructure_strategy_rejects_source_or_symbol_mismatch():
    mismatched_source = _evidence(flow_source="binance_futures_public_read_only")
    mismatched_symbol = _evidence(flow_symbol="ETH/USDT")
    assert select_strategy(
        MarketType.SPOT, "bullish", [], microstructure_analysis=mismatched_source,
    )["key"] == "none"
    assert select_strategy(
        MarketType.SPOT, "bullish", [], microstructure_analysis=mismatched_symbol,
    )["key"] == "none"


def test_microstructure_strategy_rejects_weak_conflicting_or_wide_spread_evidence():
    weak = _evidence(depth=0.1, flow=0.25)
    conflicting = _evidence(depth=0.3, flow=-0.25)
    wide_spread = _evidence(spread=0.21)
    for evidence in (weak, conflicting, wide_spread):
        result = select_strategy(
            MarketType.SPOT, "bullish", [], microstructure_analysis=evidence,
        )
        assert result["key"] == "none"


def test_microstructure_strategy_never_selects_without_directional_bias():
    result = select_strategy(
        MarketType.SPOT, "neutral", [], microstructure_analysis=_evidence(),
    )
    assert result["key"] == "none"


def test_microstructure_strategy_rejects_missing_symbol_and_out_of_range_metrics():
    missing_symbol = _evidence(liquidity_symbol="", flow_symbol="")
    invalid_depth = _evidence(depth=1.5)
    malformed = {"liquidity": ["not", "an", "object"], "order_flow": {}}
    for evidence in (missing_symbol, invalid_depth, malformed):
        result = select_strategy(
            MarketType.SPOT, "bullish", [], microstructure_analysis=evidence,
        )
        assert result["key"] == "none"


def test_futures_microstructure_requires_futures_public_source():
    result = select_strategy(
        MarketType.FUTURES, "bearish", [],
        microstructure_analysis=_evidence(depth=-0.3, flow=-0.25),
    )
    assert result["key"] == "none"


def test_microstructure_strategy_rejects_boolean_metrics():
    result = select_strategy(
        MarketType.SPOT, "bullish", [],
        microstructure_analysis=_evidence(depth=True),
    )
    assert result["key"] == "none"



def test_microstructure_strategy_rejects_stale_or_incoherent_timestamps():
    stale_book = _evidence(book_age_seconds=121)
    stale_flow = _evidence(last_trade_age_seconds=121)
    incoherent = _evidence(book_age_seconds=0, last_trade_age_seconds=121)
    missing_book_time = _evidence()
    missing_book_time["liquidity"].pop("fetched_at")
    future_flow = _evidence(last_trade_age_seconds=-10)

    for evidence in (stale_book, stale_flow, incoherent, missing_book_time, future_flow):
        result = select_strategy(
            MarketType.SPOT, "bullish", [], microstructure_analysis=evidence,
        )
        assert result["key"] == "none"

def test_microstructure_strategy_rejects_evidence_for_another_requested_symbol():
    evidence = _evidence(liquidity_symbol="ETH/USDT", flow_symbol="ETHUSDT")
    result = select_strategy(
        MarketType.SPOT, "bullish", [], microstructure_analysis=evidence,
        expected_symbol="BTC/USDT",
    )
    assert result["key"] == "none"


def test_microstructure_strategy_accepts_normalized_requested_symbol_match():
    evidence = _evidence(liquidity_symbol="BTC/USDT", flow_symbol="BTCUSDT")
    result = select_strategy(
        MarketType.SPOT, "bullish", [], microstructure_analysis=evidence,
        expected_symbol="BTCUSDT",
    )
    assert result["key"] == "liquidity_orderflow"
