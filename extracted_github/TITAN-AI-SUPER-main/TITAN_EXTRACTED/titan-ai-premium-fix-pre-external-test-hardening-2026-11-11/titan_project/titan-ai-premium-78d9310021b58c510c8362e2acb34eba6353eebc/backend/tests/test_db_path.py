from app.models.db import _ensure_sqlite_parent


def test_sqlite_parent_directory_is_created_for_relative_database(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    database_url = "sqlite:///backend/data/nested/titan.db"

    _ensure_sqlite_parent(database_url)

    assert (tmp_path / "backend" / "data" / "nested").is_dir()


def test_sqlite_memory_database_does_not_create_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    _ensure_sqlite_parent("sqlite:///:memory:")

    assert list(tmp_path.iterdir()) == []
