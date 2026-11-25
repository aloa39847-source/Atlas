from app.knowledge.verification import KnowledgeSourceVerifier


class _Response:
    status = 200
    headers = {"Content-Type": "text/html; charset=utf-8"}

    def __init__(self, url):
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self.url

    def read(self, limit):
        return (
            b"<html><head><title>Bitcoin network development and security report</title></head>"
            b"<body>Bitcoin network development activity and security report. "
            b"The network has active developers, protocol upgrades, transaction activity, "
            b"public governance records and transparent documentation for market participants.</body></html>"
        )


class _Opener:
    def open(self, request, timeout):
        return _Response(request.full_url)


def test_knowledge_verifier_checks_allowlisted_https_and_text_relevance(monkeypatch):
    verifier = KnowledgeSourceVerifier(trusted_domains={"example.org"})
    monkeypatch.setattr(
        verifier, "_fetch_pinned_https",
        lambda url: (
            url, 200, {"content-type": "text/html; charset=utf-8"},
            _Response(url).read(1_000_001),
        ),
    )
    result = verifier.verify(
        "https://docs.example.org/research",
        claim="Bitcoin network development and security report",
    )
    assert result["ok"] is True
    assert result["source_verified"] is True
    assert result["claim_relevance"] >= 0.2
    assert result["verified"] is False
    assert result["human_fact_verification"] is False
    assert len(result["content_sha256"]) == 64


def test_knowledge_verifier_rejects_untrusted_or_unsafe_urls():
    verifier = KnowledgeSourceVerifier(trusted_domains={"example.org"})
    assert verifier.verify("https://evil.example/data")["error"] == "knowledge_source_domain_not_allowlisted"
    assert verifier.verify("http://docs.example.org/data")["error"] == "unsafe_knowledge_source_url"
    assert verifier.verify("https://user:pass@docs.example.org/data")["error"] == "unsafe_knowledge_source_url"


def test_knowledge_verifier_blocks_non_public_dns_targets(monkeypatch):
    verifier = KnowledgeSourceVerifier(trusted_domains={"example.org"})
    monkeypatch.setattr(
        "app.knowledge.verification.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("127.0.0.1", 443))],
    )
    try:
        verifier._validate_public_dns("docs.example.org")
    except RuntimeError as exc:
        assert str(exc) == "knowledge_source_resolved_to_non_public_address"
    else:
        raise AssertionError("non-public DNS result must be rejected")


def test_knowledge_dns_validation_returns_only_public_pinned_addresses(monkeypatch):
    verifier = KnowledgeSourceVerifier(trusted_domains={"example.org"})
    monkeypatch.setattr(
        "app.knowledge.verification.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (None, None, None, None, ("93.184.216.34", 443)),
            (None, None, None, None, ("93.184.216.34", 443)),
        ],
    )
    assert verifier._validate_public_dns("docs.example.org") == ["93.184.216.34"]


def test_knowledge_fetch_connects_to_validated_ip_and_keeps_tls_hostname(monkeypatch):
    verifier = KnowledgeSourceVerifier(trusted_domains={"example.org"})
    connections = []
    tls_hosts = []
    class FakeSocket:
        def sendall(self, data):
            assert b"Host: docs.example.org" in data
        def close(self): pass
    class FakeContext:
        def wrap_socket(self, raw_socket, *, server_hostname):
            tls_hosts.append(server_hostname)
            return FakeSocket()
    class FakeResponse:
        status = 200
        def __init__(self, sock): pass
        def begin(self): pass
        def getheaders(self):
            return [("Content-Type", "text/html; charset=utf-8")]
        def read(self, limit):
            return _Response("https://docs.example.org/research").read(limit)
        def close(self): pass

    monkeypatch.setattr(
        "app.knowledge.verification.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )
    monkeypatch.setattr(
        "app.knowledge.verification.socket.create_connection",
        lambda address, timeout: (connections.append(address) or FakeSocket()),
    )
    monkeypatch.setattr(
        "app.knowledge.verification.ssl.create_default_context", lambda: FakeContext(),
    )
    monkeypatch.setattr(
        "app.knowledge.verification.http.client.HTTPResponse", FakeResponse,
    )
    result = verifier.verify("https://docs.example.org/research")
    assert result["ok"] is True
    assert connections == [("93.184.216.34", 443)]
    assert tls_hosts == ["docs.example.org"]


def test_knowledge_verifier_rejects_malformed_or_out_of_range_ports():
    verifier = KnowledgeSourceVerifier(trusted_domains={"example.org"})
    assert verifier.verify("https://docs.example.org:99999/data")["error"] == "unsafe_knowledge_source_url"
    assert verifier.verify("https://docs.example.org:notaport/data")["error"] == "unsafe_knowledge_source_url"


def test_knowledge_verifier_rejects_malformed_ipv6_authority():
    verifier = KnowledgeSourceVerifier(trusted_domains={"example.org"})
    assert verifier.verify("https://[invalid-ipv6/data")["error"] == "unsafe_knowledge_source_url"
