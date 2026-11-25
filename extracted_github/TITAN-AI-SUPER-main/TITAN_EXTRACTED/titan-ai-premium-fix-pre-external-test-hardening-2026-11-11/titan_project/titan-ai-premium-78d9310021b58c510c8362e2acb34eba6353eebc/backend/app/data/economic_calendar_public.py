"""Public US macroeconomic release calendar adapter with publisher provenance."""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class _SameHostHttpsRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if (old.scheme != "https" or new.scheme != "https" or not new.hostname
                or new.hostname.lower() != (old.hostname or "").lower()
                or new.port not in (None, 443)
                or new.username is not None or new.password is not None):
            raise urllib.error.HTTPError(
                req.full_url, code, "cross_host_or_unsafe_calendar_redirect_blocked", headers, fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class EconomicCalendarPublicData:
    BASE = "https://xoomar.com"
    ALLOWED_SOURCES = {"bls", "bea", "census", "fed", "dol", "treasury"}
    ALLOWED_IMPORTANCE = {"high", "med", "low"}

    def fetch(self, *, start_date: date, end_date: date, importance: str = "high") -> dict[str, Any]:
        if (not isinstance(start_date, date) or isinstance(start_date, datetime)
                or not isinstance(end_date, date) or isinstance(end_date, datetime)
                or end_date < start_date or (end_date - start_date).days > 92):
            raise ValueError("invalid_economic_calendar_date_range")
        normalized_importance = str(importance).strip().lower()
        if normalized_importance not in self.ALLOWED_IMPORTANCE | {"all"}:
            raise ValueError("invalid_economic_calendar_importance")
        params = {
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
        }
        if normalized_importance != "all":
            params["importance"] = normalized_importance
        request = urllib.request.Request(
            self.BASE + "/api/markets/calendar?" + urllib.parse.urlencode(params),
            headers={
                "User-Agent": "TITAN-AI-PREMIUM/1.0",
                "Accept": "application/json",
                **({"X-API-Key": os.getenv("XOOMAR_API_KEY", "")} if os.getenv("XOOMAR_API_KEY") else {}),
            },
        )
        try:
            opener = urllib.request.build_opener(_SameHostHttpsRedirect())
            with opener.open(request, timeout=10) as response:
                final = urllib.parse.urlsplit(response.geturl())
                if final.scheme != "https" or (final.hostname or "").lower() != "xoomar.com":
                    raise RuntimeError("economic_calendar_final_url_mismatch")
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise RuntimeError("economic_calendar_response_too_large")
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"economic_calendar_public_data_unavailable:{type(exc).__name__}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise RuntimeError("economic_calendar_invalid_response")
        events = []
        for item in payload["data"]:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source", "")).lower()
            name = str(item.get("eventName", "")).strip()
            importance_value = str(item.get("importance", "")).lower()
            scheduled = item.get("scheduledAt")
            if source not in self.ALLOWED_SOURCES or not name or importance_value not in self.ALLOWED_IMPORTANCE:
                continue
            if not isinstance(scheduled, str):
                continue
            try:
                scheduled_dt = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
                if scheduled_dt.tzinfo is None:
                    raise ValueError("timezone_required")
                scheduled_dt = scheduled_dt.astimezone(timezone.utc)
            except (TypeError, ValueError, OverflowError):
                continue
            events.append({
                "source": source,
                "event_name": name[:200],
                "importance": importance_value,
                "scheduled_at": scheduled_dt.isoformat(),
                "period_label": str(item.get("periodLabel") or "")[:80] or None,
                "previous": item.get("previous"),
                "forecast": None,  # Provider contract explicitly has no consensus column.
                "actual": item.get("actual"),
                "unit": str(item.get("unit") or "")[:80] or None,
                "canonical_url": item.get("canonicalUrl"),
            })
        events.sort(key=lambda event: event["scheduled_at"])
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        return {
            "ok": True,
            "events": events,
            "count": len(events),
            "next_release_at": payload.get("nextReleaseAt"),
            "updated_at": payload.get("updatedAt"),
            "provider": "XOOMAR",
            "provider_source": payload.get("source"),
            "source_attribution": meta.get("sourceAttribution") or payload.get("attribution"),
            "license": payload.get("license"),
            "attribution_required": True,
            "source_scope": "US macroeconomic release schedules from BLS, BEA, Census, Federal Reserve, DOL and Treasury",
            "forecast_consensus_available": False,
            "limitations": [
                "US macroeconomic calendar only; not a global economic calendar",
                "forecast/consensus values are not supplied by this provider",
                "actual and previous values may populate after the release and may be revised",
                "provider aggregates official agency schedules; attribution and source context must be retained",
            ],
        }
