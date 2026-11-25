"""Allowlisted DeFiLlama public protocol TVL, fees and revenue adapter."""
from __future__ import annotations

import json
import re
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
            raise RuntimeError("cross_host_or_unsafe_defillama_redirect_blocked")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class DefiLlamaPublicData:
    BASE = "https://api.llama.fi"

    def _get_json(self, path: str) -> Any:
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            raise ValueError("invalid_defillama_path")
        request = urllib.request.Request(
            f"{self.BASE}{path}",
            headers={"User-Agent": "TITAN-AI-PREMIUM/1.0", "Accept": "application/json"},
        )
        try:
            opener = urllib.request.build_opener(_SameHostHttpsRedirect())
            with opener.open(request, timeout=10) as response:
                raw = response.read(3_000_001)
            if len(raw) > 3_000_000:
                raise RuntimeError("defillama_response_too_large")
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"defillama_public_data_unavailable:{type(exc).__name__}") from exc

    @staticmethod
    def _slug(slug: str) -> str:
        normalized = str(slug or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9-]{1,80}", normalized):
            raise ValueError("invalid_defillama_protocol_slug")
        return normalized

    def get_protocol_overview(self, slug: str) -> dict[str, Any]:
        normalized = self._slug(slug)
        data = self._get_json("/protocol/" + urllib.parse.quote(normalized, safe="-"))
        if not isinstance(data, dict) or str(data.get("slug", "")).lower() != normalized:
            raise RuntimeError("defillama_protocol_not_found_or_mismatched")
        return data

    def get_protocol_fees(self, slug: str) -> dict[str, Any]:
        normalized = self._slug(slug)
        query = urllib.parse.urlencode({"dataType": "dailyFees"})
        data = self._get_json(
            "/summary/fees/" + urllib.parse.quote(normalized, safe="-") + "?" + query
        )
        if not isinstance(data, dict):
            raise RuntimeError("defillama_invalid_protocol_fee_summary")
        return data
