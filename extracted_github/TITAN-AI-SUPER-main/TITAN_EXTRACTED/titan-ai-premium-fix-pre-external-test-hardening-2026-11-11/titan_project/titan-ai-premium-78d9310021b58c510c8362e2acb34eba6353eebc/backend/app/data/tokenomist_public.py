"""Tokenomist token unlock schedule adapter; requires a licensed API key."""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
import re
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
                req.full_url, code, "cross_host_or_unsafe_tokenomist_redirect_blocked", headers, fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class TokenomistPublicData:
    BASE = "https://api.unlocks.app"

    def __init__(self, api_key: str | None = None):
        self.api_key = (api_key if api_key is not None else os.getenv("TOKENOMIST_API_KEY", "")).strip()

    def _get_json(self, path: str) -> Any:
        if not self.api_key:
            raise RuntimeError("tokenomist_api_key_required")
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            raise ValueError("invalid_tokenomist_path")
        request = urllib.request.Request(
            self.BASE + path,
            headers={
                "User-Agent": "TITAN-AI-PREMIUM/1.0",
                "Accept": "application/json",
                "x-api-key": self.api_key,
            },
        )
        try:
            opener = urllib.request.build_opener(_SameHostHttpsRedirect())
            with opener.open(request, timeout=10) as response:
                final = urllib.parse.urlsplit(response.geturl())
                if final.scheme != "https" or (final.hostname or "").lower() != "api.unlocks.app":
                    raise RuntimeError("tokenomist_final_url_mismatch")
                raw = response.read(3_000_001)
                if len(raw) > 3_000_000:
                    raise RuntimeError("tokenomist_response_too_large")
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"tokenomist_public_data_unavailable:{type(exc).__name__}") from exc

    def get_token_list(self) -> list[dict[str, Any]]:
        payload = self._get_json("/v4/token/list")
        if not isinstance(payload, dict) or payload.get("status") is not True or not isinstance(payload.get("data"), list):
            raise RuntimeError("tokenomist_invalid_token_list")
        output = []
        for item in payload["data"]:
            if not isinstance(item, dict):
                continue
            token_id = str(item.get("id", "")).strip().lower()
            symbol = str(item.get("symbol", "")).strip().upper()
            if not re.fullmatch(r"[a-z0-9-]{1,80}", token_id) or not re.fullmatch(r"[A-Z0-9]{1,20}", symbol):
                continue
            output.append({
                "id": token_id,
                "name": str(item.get("name") or "")[:120],
                "symbol": symbol,
                "listed_method": str(item.get("listedMethod") or "UNKNOWN").upper(),
                "circulating_supply": item.get("circulatingSupply"),
                "market_cap": item.get("marketCap"),
                "max_supply": item.get("maxSupply"),
                "total_locked_amount": item.get("totalLockedAmount"),
                "unlocked_amount": item.get("unlockedAmount"),
                "last_updated_date": item.get("lastUpdatedDate"),
                "website_url": item.get("websiteUrl"),
            })
        return output

    def get_unlock_events(
        self, token_id: str, *, start: date, end: date,
    ) -> dict[str, Any]:
        normalized = str(token_id or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9-]{1,80}", normalized):
            raise ValueError("invalid_tokenomist_token_id")
        if (not isinstance(start, date) or isinstance(start, datetime)
                or not isinstance(end, date) or isinstance(end, datetime)
                or end < start or (end - start).days > 1100):
            raise ValueError("invalid_tokenomist_unlock_date_range")
        query = urllib.parse.urlencode({
            "tokenId": normalized, "start": start.isoformat(), "end": end.isoformat(),
        })
        payload = self._get_json("/v4/unlock/events?" + query)
        if not isinstance(payload, dict) or payload.get("status") is not True or not isinstance(payload.get("data"), list):
            raise RuntimeError("tokenomist_invalid_unlock_events")
        events = []
        for item in payload["data"]:
            if not isinstance(item, dict) or not isinstance(item.get("unlockDate"), str):
                continue
            try:
                unlock_dt = datetime.fromisoformat(item["unlockDate"].replace("Z", "+00:00"))
                if unlock_dt.tzinfo is None:
                    continue
                unlock_dt = unlock_dt.astimezone(timezone.utc)
            except (TypeError, ValueError, OverflowError):
                continue
            cliff = item.get("cliffUnlocks")
            if not isinstance(cliff, dict):
                cliff = {}
            amount = cliff.get("cliffAmount")
            value = cliff.get("cliffValue")
            value_to_mcap = cliff.get("valueToMarketCap")
            numeric = {}
            for key, raw in (("cliff_amount", amount), ("cliff_value_usd", value),
                             ("value_to_market_cap_percent", value_to_mcap)):
                if raw is None:
                    numeric[key] = None
                    continue
                if isinstance(raw, bool):
                    numeric[key] = None
                    continue
                try:
                    number = float(raw)
                    numeric[key] = number if __import__("math").isfinite(number) and number >= 0 else None
                except (TypeError, ValueError, OverflowError):
                    numeric[key] = None
            allocations = cliff.get("allocationBreakdown")
            if not isinstance(allocations, list):
                allocations = []
            events.append({
                "unlock_date": unlock_dt.isoformat(),
                "token_name": str(item.get("tokenName") or "")[:120],
                "token_symbol": str(item.get("tokenSymbol") or "").upper()[:20],
                "listed_method": str(item.get("listedMethod") or "UNKNOWN").upper(),
                "data_source": str(item.get("dataSource") or "")[:120],
                **numeric,
                "allocation_breakdown": [
                    {
                        "allocation_name": str(row.get("allocationName") or "")[:120],
                        "standard_allocation_name": str(row.get("standardAllocationName") or "")[:120],
                        "cliff_amount": row.get("cliffAmount"),
                        "cliff_value_usd": row.get("cliffValue"),
                        "unlock_precision": row.get("unlockPrecision"),
                    }
                    for row in allocations if isinstance(row, dict)
                ],
                "latest_update_date": item.get("latestUpdateDate"),
            })
        events.sort(key=lambda event: event["unlock_date"])
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        return {
            "ok": True, "token_id": normalized, "events": events, "count": len(events),
            "query_date": metadata.get("queryDate"),
            "source": "Tokenomist API v4",
            "source_url": "https://docs.unlocks.app/api-documents/api-endpoints/unlock-events-api/unlock-events-api-v4",
            "commercial_redistribution_allowed": False,
            "limitations": [
                "API plan required; data terms restrict standard API data to personal/non-commercial, non-redistribution use",
                "v4 includes cliff unlock events and intentionally excludes linear unlock starts and mining/yield-farming distributions",
                "listedMethod AI may rely on one AI-assisted source; INTERNAL indicates provider cross-checking, not a guarantee",
                "unlock schedules can be revised; latestUpdateDate and dataSource must be retained",
            ],
        }
