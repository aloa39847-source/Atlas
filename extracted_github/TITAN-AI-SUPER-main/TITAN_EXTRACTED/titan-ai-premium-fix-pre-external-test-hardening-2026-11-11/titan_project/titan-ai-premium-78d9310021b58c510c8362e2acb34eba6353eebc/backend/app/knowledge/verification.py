"""Independent source accessibility and claim-relevance checks for knowledge records.

A successful fetch is not proof that a claim is true. This verifier records
source identity, retrieval provenance and text relevance while keeping the
knowledge record's human verification flag false.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import http.client
import ipaddress
import os
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_TRUSTED_DOMAINS = {
    "binance.com", "binance.vision", "coingecko.com", "defillama.com",
    "ethereum.org", "bitcoin.org", "federalreserve.gov", "sec.gov",
    "cftc.gov", "investor.gov", "bis.org", "imf.org", "worldbank.org",
    "github.com",
}
STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was",
    "were", "will", "has", "have", "had", "into", "about", "over", "under",
    "its", "their", "they", "them", "than", "then", "when", "what", "which",
    "who", "why", "how", "not", "but", "can", "may", "more", "less",
}


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.in_title = False
        self.hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self.in_title = True
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data):
        value = re.sub(r"\s+", " ", data or "").strip()
        if not value:
            return
        if self.in_title:
            self.title_parts.append(value)
        if not self.hidden_depth:
            self.text_parts.append(value)


class _SameHostHttpsRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if (old.scheme != "https" or new.scheme != "https" or not new.hostname
                or new.hostname.lower() != (old.hostname or "").lower()
                or new.port not in (None, 443)
                or new.username is not None or new.password is not None):
            raise urllib.error.HTTPError(
                req.full_url, code, "cross_host_or_unsafe_knowledge_redirect_blocked", headers, fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class KnowledgeSourceVerifier:
    def __init__(self, trusted_domains: set[str] | None = None, timeout: float = 8.0, max_bytes: int = 1_000_000):
        configured = os.getenv("TITAN_KNOWLEDGE_TRUSTED_DOMAINS", "")
        env_domains = {part.strip().lower() for part in configured.split(",") if part.strip()}
        self.trusted_domains = {
            domain.lower().strip(".") for domain in (
                trusted_domains if trusted_domains is not None
                else (DEFAULT_TRUSTED_DOMAINS | env_domains)
            )
        }
        self.timeout = timeout
        self.max_bytes = max_bytes

    def _validate_url(self, url: str) -> tuple[str, str]:
        if not isinstance(url, str) or len(url) > 2048:
            raise ValueError("invalid_knowledge_source_url")
        try:
            parsed = urllib.parse.urlsplit(url)
            port = parsed.port
            host = (parsed.hostname or "").lower().rstrip(".")
        except ValueError as exc:
            raise ValueError("unsafe_knowledge_source_url") from exc
        if (parsed.scheme != "https" or not host or parsed.username is not None
                or parsed.password is not None or port not in (None, 443)
                or parsed.fragment):
            raise ValueError("unsafe_knowledge_source_url")
        if not any(host == domain or host.endswith("." + domain) for domain in self.trusted_domains):
            raise ValueError("knowledge_source_domain_not_allowlisted")
        return host, urllib.parse.urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))

    @staticmethod
    def _validate_public_dns(host: str) -> list[str]:
        try:
            records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise RuntimeError("knowledge_source_dns_resolution_failed") from exc
        addresses = sorted({record[4][0] for record in records})
        if not addresses:
            raise RuntimeError("knowledge_source_dns_empty")
        for address in addresses:
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError as exc:
                raise RuntimeError("knowledge_source_dns_invalid") from exc
            if not parsed.is_global:
                raise RuntimeError("knowledge_source_resolved_to_non_public_address")
        return addresses

    def _fetch_pinned_https(self, url: str) -> tuple[str, int, dict[str, str], bytes]:
        current_url = url
        for redirect_count in range(4):
            host, safe_url = self._validate_url(current_url)
            addresses = self._validate_public_dns(host)
            parsed = urllib.parse.urlsplit(safe_url)
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            tls_socket = None
            response = None
            try:
                raw_socket = socket.create_connection((addresses[0], 443), timeout=self.timeout)
                context = ssl.create_default_context()
                tls_socket = context.wrap_socket(raw_socket, server_hostname=host)
                request = (
                    f"GET {target} HTTP/1.1\r\n"
                    f"Host: {host}\r\n"
                    "User-Agent: TITAN-AI-PREMIUM-KnowledgeVerifier/1.0\r\n"
                    "Accept: text/html, text/plain;q=0.9\r\n"
                    "Accept-Encoding: identity\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
                tls_socket.sendall(request)
                response = http.client.HTTPResponse(tls_socket)
                response.begin()
                status = int(response.status)
                headers = {key.lower(): value for key, value in response.getheaders()}
                if status in {301, 302, 303, 307, 308}:
                    location = headers.get("location")
                    if not location or redirect_count >= 3:
                        raise RuntimeError("knowledge_source_redirect_limit_or_location_invalid")
                    redirected = urllib.parse.urljoin(safe_url, location)
                    redirect_host, current_url = self._validate_url(redirected)
                    if redirect_host != host:
                        raise RuntimeError("cross_host_knowledge_redirect_blocked")
                    continue
                body = response.read(self.max_bytes + 1)
                return safe_url, status, headers, body
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                raise RuntimeError("knowledge_source_pinned_https_fetch_failed") from exc
            finally:
                if response is not None:
                    response.close()
                if tls_socket is not None:
                    tls_socket.close()
        raise RuntimeError("knowledge_source_redirect_limit_exceeded")

    @staticmethod
    def _relevance(claim: str, text: str) -> float | None:
        claim_tokens = {
            token for token in re.findall(r"[a-z0-9]{2,}", claim.lower())
            if token not in STOP_WORDS
        }
        if not claim_tokens:
            return None
        page_tokens = set(re.findall(r"[a-z0-9]{2,}", text.lower()))
        return round(len(claim_tokens & page_tokens) / len(claim_tokens), 4)

    def verify(self, url: str, *, claim: str | None = None) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            safe_url, status, headers, raw = self._fetch_pinned_https(url)
            host = urllib.parse.urlsplit(safe_url).hostname or ""
            content_type = str(headers.get("content-type", "")).split(";")[0].strip().lower()
            if status != 200:
                raise RuntimeError("knowledge_source_http_status_not_ok")
            if content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
                raise RuntimeError("knowledge_source_unsupported_content_type")
            if len(raw) > self.max_bytes:
                raise RuntimeError("knowledge_source_content_too_large")
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                raise RuntimeError("knowledge_source_dtd_or_entity_rejected")
            decoded = raw.decode("utf-8", errors="replace")
            parser = _VisibleText()
            parser.feed(decoded)
            title = re.sub(r"\s+", " ", " ".join(parser.title_parts)).strip()[:256]
            visible_text = re.sub(r"\s+", " ", " ".join(parser.text_parts)).strip()
            if not title or len(visible_text) < 80:
                raise RuntimeError("knowledge_source_content_insufficient")
            relevance = self._relevance(claim or "", visible_text) if claim else None
            if claim and relevance is None:
                relevance_status = "claim_tokens_not_available"
            elif claim and relevance < 0.20:
                relevance_status = "claim_relevance_low"
            elif claim:
                relevance_status = "claim_relevance_supported_by_text_overlap"
            else:
                relevance_status = "claim_not_supplied"
            return {
                "ok": True,
                "source_verified": True,
                "human_fact_verification": False,
                "verified": False,
                "host": host,
                "final_url": safe_url,
                "http_status": status,
                "content_type": content_type,
                "page_title": title,
                "claim_relevance": relevance,
                "claim_relevance_status": relevance_status,
                "content_sha256": hashlib.sha256(raw).hexdigest(),
                "content_bytes": len(raw),
                "checked_at": checked_at,
                "method": "allowlisted_https_fetch_dns_public_ip_same_host_redirect_title_and_text_overlap",
                "note": "Source accessibility and text overlap do not independently prove factual truth; human fact verification remains false.",
            }
        except Exception as exc:
            return {
                "ok": False,
                "source_verified": False,
                "human_fact_verification": False,
                "verified": False,
                "error": str(exc)[:200],
                "checked_at": checked_at,
            }
