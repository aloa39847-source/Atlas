import os

from app.core.config import load_project_environment


def test_project_env_loader_reads_root_file_without_overriding_process_environment(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "TITAN_APP_ENV=from-file\nTITAN_AUTH_REQUIRED=false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TITAN_APP_ENV", "from-process")
    monkeypatch.delenv("TITAN_AUTH_REQUIRED", raising=False)

    load_project_environment(tmp_path)

    assert os.environ["TITAN_APP_ENV"] == "from-process"
    assert os.environ["TITAN_AUTH_REQUIRED"] == "false"
