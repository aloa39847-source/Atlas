from datetime import date
import pytest

from app.data.tokenomist_public import TokenomistPublicData, _SameHostHttpsRedirect


def test_tokenomist_requires_api_key_before_network():
    provider = TokenomistPublicData(api_key="")
    with pytest.raises(RuntimeError, match="tokenomist_api_key_required"):
        provider.get_token_list()


def test_tokenomist_token_list_normalizes_provider_ids_and_listing_method(monkeypatch):
    provider = TokenomistPublicData(api_key="test-key")
    monkeypatch.setattr(provider, "_get_json", lambda path: {
        "status": True,
        "data": [
            {"id": "arbitrum", "name": "Arbitrum", "symbol": "ARB", "listedMethod": "INTERNAL"},
            {"id": "../bad", "name": "Bad", "symbol": "BAD"},
        ],
    })
    result = provider.get_token_list()
    assert len(result) == 1
    assert result[0]["id"] == "arbitrum"
    assert result[0]["listed_method"] == "INTERNAL"


def test_tokenomist_unlock_events_parse_cliffs_and_retain_provenance(monkeypatch):
    provider = TokenomistPublicData(api_key="test-key")
    monkeypatch.setattr(provider, "_get_json", lambda path: {
        "status": True,
        "metadata": {"queryDate": "2026-11-08T12:00:00Z"},
        "data": [
            {
                "unlockDate": "2026-11-10T00:00:00Z",
                "tokenName": "Arbitrum", "tokenSymbol": "ARB",
                "listedMethod": "INTERNAL", "dataSource": "Whitepaper",
                "cliffUnlocks": {
                    "cliffAmount": 1000000, "cliffValue": 500000,
                    "valueToMarketCap": 1.2,
                    "allocationBreakdown": [{
                        "allocationName": "Investors",
                        "standardAllocationName": "Private Investors",
                        "cliffAmount": 1000000, "cliffValue": 500000,
                        "unlockPrecision": "day",
                    }],
                },
                "latestUpdateDate": "2026-11-07T00:00:00Z",
            },
        ],
    })
    result = provider.get_unlock_events("arbitrum", start=date(2026, 11, 1), end=date(2026, 12, 1))
    assert result["ok"] is True
    assert result["events"][0]["cliff_amount"] == 1000000
    assert result["events"][0]["value_to_market_cap_percent"] == 1.2
    assert result["events"][0]["allocation_breakdown"][0]["allocation_name"] == "Investors"
    assert result["commercial_redistribution_allowed"] is False


def test_tokenomist_rejects_bad_token_id_and_date_range():
    provider = TokenomistPublicData(api_key="test-key")
    with pytest.raises(ValueError, match="invalid_tokenomist_token_id"):
        provider.get_unlock_events("../bad", start=date(2026, 1, 1), end=date(2026, 2, 1))
    with pytest.raises(ValueError, match="invalid_tokenomist_unlock_date_range"):
        provider.get_unlock_events("arbitrum", start=date(2020, 1, 1), end=date(2024, 1, 1))


def test_tokenomist_redirect_policy_blocks_cross_host_redirects():
    from urllib.request import Request
    handler = _SameHostHttpsRedirect()
    request = Request("https://api.unlocks.app/v4/token/list")
    with pytest.raises(Exception, match="cross_host_or_unsafe_tokenomist_redirect_blocked"):
        handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/data")
