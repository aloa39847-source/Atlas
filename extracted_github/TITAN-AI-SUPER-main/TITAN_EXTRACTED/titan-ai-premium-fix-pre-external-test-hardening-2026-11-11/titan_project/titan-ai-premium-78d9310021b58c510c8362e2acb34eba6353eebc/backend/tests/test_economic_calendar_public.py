from datetime import date

import pytest

from app.data.economic_calendar_public import EconomicCalendarPublicData, _SameHostHttpsRedirect


class _Response:
    def __init__(self, url, payload):
        self.url = url
        self.payload = payload

    def __enter__(self): return self
    def __exit__(self, *args): return False
    def geturl(self): return self.url
    def read(self, limit): return self.payload


class _Opener:
    def __init__(self, payload): self.payload = payload
    def open(self, request, timeout):
        return _Response(request.full_url, self.payload)


def test_economic_calendar_validates_events_and_preserves_agency_attribution(monkeypatch):
    import json
    payload = {
        "data": [
            {"source": "bls", "eventName": "CPI", "importance": "high",
             "scheduledAt": "2026-11-10T13:30:00Z", "periodLabel": "October 2026",
             "previous": "3.0", "actual": None, "unit": "% y/y"},
            {"source": "unknown", "eventName": "Bad source", "importance": "high",
             "scheduledAt": "2026-11-10T13:30:00Z"},
            {"source": "fed", "eventName": "Bad time", "importance": "high",
             "scheduledAt": "not-a-date"},
        ],
        "nextReleaseAt": "2026-11-10T13:30:00Z",
        "updatedAt": "2026-11-08T12:00:00Z",
        "source": "official_agency_schedules",
        "meta": {"sourceAttribution": "BLS, BEA, Census, Federal Reserve"},
        "license": "attribution-required",
    }
    monkeypatch.setattr(
        "app.data.economic_calendar_public.urllib.request.build_opener",
        lambda *handlers: _Opener(json.dumps(payload).encode()),
    )
    result = EconomicCalendarPublicData().fetch(
        start_date=date(2026, 11, 8), end_date=date(2026, 11, 30), importance="high",
    )
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["events"][0]["event_name"] == "CPI"
    assert result["events"][0]["forecast"] is None
    assert result["source_attribution"] == "BLS, BEA, Census, Federal Reserve"
    assert result["attribution_required"] is True


def test_economic_calendar_rejects_invalid_date_ranges_and_importance():
    provider = EconomicCalendarPublicData()
    with pytest.raises(ValueError, match="invalid_economic_calendar_date_range"):
        provider.fetch(start_date=date(2026, 1, 1), end_date=date(2026, 5, 1))
    with pytest.raises(ValueError, match="invalid_economic_calendar_importance"):
        provider.fetch(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2), importance="critical")


def test_economic_calendar_redirect_policy_blocks_cross_host_redirects():
    from urllib.request import Request
    handler = _SameHostHttpsRedirect()
    request = Request("https://xoomar.com/api/markets/calendar")
    with pytest.raises(Exception, match="cross_host_or_unsafe_calendar_redirect_blocked"):
        handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/data")
