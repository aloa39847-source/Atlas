from app.data.coingecko_public import CoinGeckoPublicData
from app.engines.fundamental import FundamentalAnalysisEngine
import pytest


def test_fundamental_engine_calculates_tokenomics_and_market_ratios(monkeypatch):
    snapshot = {
        "id": "bitcoin", "symbol": "btc", "name": "Bitcoin",
        "current_price": 100.0, "market_cap": 1_000_000.0,
        "total_volume": 100_000.0, "fully_diluted_valuation": 2_000_000.0,
        "circulating_supply": 100.0, "total_supply": 150.0, "max_supply": 200.0,
        "ath": 200.0, "price_change_percentage_24h_in_currency": 2.5,
        "price_change_percentage_7d_in_currency": -1.0,
        "price_change_percentage_30d_in_currency": 4.0,
        "market_cap_rank": 1, "last_updated": "2026-11-08T12:00:00Z",
    }
    monkeypatch.setattr(CoinGeckoPublicData, "get_coin_market_data", lambda self, coin_id: snapshot)
    result = FundamentalAnalysisEngine().fetch_and_analyze("bitcoin")
    assert result["ok"] is True
    assert result["circulating_to_max_supply_ratio"] == 0.5
    assert result["fdv_to_market_cap_ratio"] == 2.0
    assert result["market_cap_to_volume_24h_ratio"] == 10.0
    assert result["drawdown_from_ath_percent"] == 50.0
    assert result["signal"] == "not_generated"
    assert result["execution"] == "none"


def test_fundamental_engine_rejects_missing_or_invalid_market_snapshot():
    engine = FundamentalAnalysisEngine()
    assert engine.analyze_snapshot({})["error"] == "coin_market_snapshot_required"
    assert engine.analyze_snapshot({"id": "x", "current_price": 0})["error"] == "valid_current_market_price_required"
    invalid = engine.analyze_snapshot({"id": "x", "current_price": 10, "market_cap": -1})
    assert invalid["error"] == "negative_market_or_supply_metrics_rejected"


def test_coingecko_adapter_validates_coin_id_before_network(monkeypatch):
    adapter = CoinGeckoPublicData()
    monkeypatch.setattr(adapter, "_get_json", lambda path: [{"id": "bitcoin"}])
    assert adapter.get_coin_market_data("bitcoin")["id"] == "bitcoin"
    with pytest.raises(ValueError, match="invalid_coin_id"):
        adapter.get_coin_market_data("../bitcoin")


def test_fundamental_engine_does_not_treat_boolean_as_market_price():
    result = FundamentalAnalysisEngine().analyze_snapshot({"id": "coin", "current_price": True})
    assert result["ok"] is False
    assert result["error"] == "valid_current_market_price_required"


def test_fundamental_analysis_suppresses_overflowed_ratios_and_flags_ath_inconsistency():
    engine = FundamentalAnalysisEngine()
    result = engine.analyze_snapshot({
        "id": "extreme-token",
        "symbol": "EXT",
        "current_price": 1e308,
        "market_cap": 1e308,
        "total_volume": 1e-308,
        "fully_diluted_valuation": 1e308,
        "circulating_supply": 1e308,
        "total_supply": 1e308,
        "max_supply": 1e-308,
        "ath": 1e-308,
    })
    assert result["ok"] is True
    assert result["market_cap_to_volume_24h_ratio"] is None
    assert result["fdv_to_market_cap_ratio"] == 1.0
    assert result["circulating_to_max_supply_ratio"] is None
    assert result["drawdown_from_ath_percent"] is None
    assert "current_price_exceeds_reported_all_time_high" in result["market_data_warnings"]


def test_protocol_fundamental_engine_calculates_defillama_tvl_and_fee_metrics():
    from datetime import datetime, timezone
    now = int(datetime.now(timezone.utc).timestamp())
    protocol = {
        "slug": "sample-protocol",
        "name": "Sample Protocol",
        "category": "Lending",
        "chains": ["Ethereum", "Arbitrum", "Ethereum"],
        "tvl": [
            {"date": now - 40 * 86400, "totalLiquidityUSD": 60.0},
            {"date": now - 31 * 86400, "totalLiquidityUSD": 80.0},
            {"date": now - 8 * 86400, "totalLiquidityUSD": 100.0},
            {"date": now, "totalLiquidityUSD": 120.0},
        ],
    }
    fees = {
        "total24h": 10.0, "total7d": 70.0, "total30d": 300.0,
        "totalAllTime": 1000.0, "totalRevenue24h": 3.0,
        "totalRevenue7d": 21.0, "totalRevenue30d": 90.0,
    }
    result = FundamentalAnalysisEngine().analyze_protocol_snapshot(protocol, fees)
    assert result["ok"] is True
    assert result["current_tvl_usd"] == 120.0
    assert result["tvl_change_7d_percent"] == 20.0
    assert result["tvl_change_30d_percent"] == 50.0
    assert result["fees_30d_usd"] == 300.0
    assert result["fees_30d_to_current_tvl_percent"] == 250.0
    assert result["chains"] == ["Arbitrum", "Ethereum"]
    assert result["signal"] == "not_generated"


def test_protocol_fundamental_engine_rejects_stale_duplicate_and_invalid_snapshots():
    from datetime import datetime, timezone
    now = int(datetime.now(timezone.utc).timestamp())
    engine = FundamentalAnalysisEngine()
    stale = {
        "slug": "sample", "tvl": [{"date": now - 10 * 86400, "totalLiquidityUSD": 100}],
    }
    assert engine.analyze_protocol_snapshot(stale, {})["error"] == "stale_or_future_protocol_tvl_snapshot"
    duplicate = {
        "slug": "sample", "tvl": [
            {"date": now, "totalLiquidityUSD": 100},
            {"date": now, "totalLiquidityUSD": 120},
        ],
    }
    assert engine.analyze_protocol_snapshot(duplicate, {})["error"] == "valid_unique_protocol_tvl_history_required"


