from app.knowledge.validation import validate_knowledge_source


def test_knowledge_source_is_unverified_even_when_client_claims_verified():
    result = validate_knowledge_source({
        "title": "Exchange documentation",
        "source_type": "official_docs",
        "url": "https://developers.binance.com/",
        "reliability_score": 0.95,
        "verified": True,
    })
    assert result["ok"] is True
    assert result["verified"] is False
    assert result["metadata"]["verification_status"] == "unverified"


def test_knowledge_source_rejects_invalid_urls_and_reliability_scores():
    assert validate_knowledge_source({"title": "x", "url": "javascript:alert(1)"})["error"] == "source_url_must_be_http_or_https"
    assert validate_knowledge_source({"title": "x", "reliability_score": float("nan")})["error"] == "reliability_score_must_be_finite_0_to_1"
    assert validate_knowledge_source({"title": "x", "reliability_score": 1.1})["error"] == "reliability_score_must_be_finite_0_to_1"
    assert validate_knowledge_source({"title": " "})["error"] == "source_title_required_max_256"


def test_knowledge_source_normalizes_optional_fields():
    result = validate_knowledge_source({"title": "  Market microstructure  ", "notes": "  Review later "})
    assert result["ok"] is True
    assert result["title"] == "Market microstructure"
    assert result["notes"] == "Review later"
    assert result["url"] is None


def test_learning_add_endpoint_never_accepts_client_verification(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app import star_app

    class FakeDB:
        def __init__(self):
            self.added = []
        def add(self, item):
            self.added.append(item)
        def commit(self):
            pass
        def refresh(self, item):
            item.id = 1
        def close(self):
            pass

    db = FakeDB()
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app, "require_user", lambda request, session: ({"id": 1}, None))
    monkeypatch.setattr(star_app, "body", lambda request: _async_payload({
        "category": "research", "subject": "RSI", "lesson": "test",
        "verified": True,
    }))
    response = asyncio.run(star_app.learning_add(SimpleNamespace()))
    assert response.status_code == 200
    assert db.added[0].verified is False


def test_knowledge_add_endpoint_never_accepts_client_verification(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app import star_app

    class FakeDB:
        def __init__(self):
            self.added = []
        def add(self, item):
            self.added.append(item)
        def commit(self):
            pass
        def refresh(self, item):
            item.id = 1
        def close(self):
            pass

    db = FakeDB()
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app, "require_user", lambda request, session: ({"id": 1}, None))
    monkeypatch.setattr(star_app, "body", lambda request: _async_payload({
        "title": "Official docs", "url": "https://example.org/docs",
        "verified": True,
    }))
    response = asyncio.run(star_app.knowledge_add(SimpleNamespace()))
    assert response.status_code == 200
    assert db.added[0].verified is False


async def _async_payload(payload):
    return payload


def test_knowledge_source_rejects_local_private_and_credential_urls():
    for url in (
        "http://localhost/private",
        "http://127.0.0.1/admin",
        "http://192.168.1.10/docs",
        "http://[::1]/docs",
        "https://user:password@example.org/docs",
        "https://example.org:99999/docs",
    ):
        result = validate_knowledge_source({"title": "unsafe source", "url": url})
        assert result["ok"] is False


def test_knowledge_source_accepts_public_https_url():
    result = validate_knowledge_source({
        "title": "Public docs", "url": "https://example.org/docs",
    })
    assert result["ok"] is True
    assert result["verified"] is False


def test_knowledge_verify_endpoint_rejects_fractional_source_ids(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app import star_app

    class FakeDB:
        def __init__(self):
            self.get_called = False
        def get(self, model, source_id):
            self.get_called = True
            return None
        def close(self):
            pass

    db = FakeDB()
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app, "require_user", lambda request, session: ({"id": 1}, None))
    monkeypatch.setattr(star_app, "body", lambda request: _async_payload({"source_id": 1.5}))
    response = asyncio.run(star_app.knowledge_verify(SimpleNamespace()))
    assert response.status_code == 400
    assert db.get_called is False
