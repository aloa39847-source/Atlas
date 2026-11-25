import pytest

from app.data.defillama_public import DefiLlamaPublicData, _SameHostHttpsRedirect


def test_defillama_adapter_uses_fixed_protocol_endpoints_and_validates_slugs(monkeypatch):
    adapter = DefiLlamaPublicData()
    seen = []
    def get_json(path):
        seen.append(path)
        if path.startswith("/protocol/"):
            return {"slug": "aave", "name": "Aave", "tvl": []}
        return {"total24h": 100.0, "total30d": 3000.0}
    monkeypatch.setattr(adapter, "_get_json", get_json)
    assert adapter.get_protocol_overview("Aave")["name"] == "Aave"
    assert adapter.get_protocol_fees("aave")["total24h"] == 100.0
    assert seen[0] == "/protocol/aave"
    assert seen[1].startswith("/summary/fees/aave?")
    with pytest.raises(ValueError, match="invalid_defillama_protocol_slug"):
        adapter.get_protocol_overview("../evil")


def test_defillama_adapter_rejects_mismatched_protocol_response(monkeypatch):
    adapter = DefiLlamaPublicData()
    monkeypatch.setattr(adapter, "_get_json", lambda path: {"slug": "other", "tvl": []})
    with pytest.raises(RuntimeError, match="protocol_not_found_or_mismatched"):
        adapter.get_protocol_overview("aave")


def test_defillama_redirect_policy_rejects_cross_host_and_http_redirects():
    from urllib.request import Request
    handler = _SameHostHttpsRedirect()
    request = Request("https://api.llama.fi/protocol/aave")
    with pytest.raises(RuntimeError, match="cross_host_or_unsafe_defillama_redirect_blocked"):
        handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/data")
    with pytest.raises(RuntimeError, match="cross_host_or_unsafe_defillama_redirect_blocked"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://api.llama.fi/data")


