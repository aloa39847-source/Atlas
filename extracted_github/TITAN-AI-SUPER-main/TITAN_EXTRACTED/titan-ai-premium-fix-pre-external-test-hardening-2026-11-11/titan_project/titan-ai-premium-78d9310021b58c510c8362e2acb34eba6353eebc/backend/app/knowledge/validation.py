from __future__ import annotations

import ipaddress
import math
from urllib.parse import urlparse
from typing import Any


def validate_knowledge_source(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate source metadata; user-submitted metadata is never self-verified."""
    if not isinstance(payload, dict):
        return {"ok": False, "error": "source_payload_must_be_object"}
    title = str(payload.get("title") or "").strip()
    if not title or len(title) > 256:
        return {"ok": False, "error": "source_title_required_max_256"}
    source_type = str(payload.get("source_type") or "manual").strip().lower()
    if not source_type or len(source_type) > 64:
        return {"ok": False, "error": "invalid_source_type"}
    raw_url = payload.get("url")
    url = str(raw_url).strip() if raw_url is not None else None
    if url:
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
            return {"ok": False, "error": "source_url_must_be_http_or_https"}
        if parsed.username is not None or parsed.password is not None:
            return {"ok": False, "error": "source_url_credentials_not_allowed"}
        try:
            parsed.port  # Validate malformed/out-of-range ports.
        except ValueError:
            return {"ok": False, "error": "source_url_invalid_port"}
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".localhost"):
            return {"ok": False, "error": "source_url_local_host_not_allowed"}
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return {"ok": False, "error": "source_url_non_public_ip_not_allowed"}
    reliability = payload.get("reliability_score")
    if reliability is not None:
        try:
            reliability = float(reliability)
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "invalid_reliability_score"}
        if not math.isfinite(reliability) or not 0.0 <= reliability <= 1.0:
            return {"ok": False, "error": "reliability_score_must_be_finite_0_to_1"}
    notes = str(payload.get("notes") or "").strip()
    return {
        "ok": True,
        "title": title,
        "source_type": source_type,
        "url": url,
        "reliability_score": reliability,
        "notes": notes,
        "verified": False,
        "metadata": {
            "verification_status": "unverified",
            "verification_note": "User-submitted source metadata is not independent source verification.",
        },
    }
