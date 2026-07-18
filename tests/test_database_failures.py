from __future__ import annotations

import logging
from pathlib import Path

from fastapi.testclient import TestClient

import app.api.main as api_main
import app.proxy.main as proxy_main
from app.db import DatabaseOperationError
from app.proxy.main import ProxySettings, create_app


_SENTINEL = "database-fixture-must-not-leak"


def _request() -> dict[str, object]:
    return {
        "model": "fixture",
        "messages": [
            {"role": "system", "content": "Unmapped fixture prompt."},
            {"role": "user", "content": "hello"},
        ],
    }


def test_proxy_completion_survives_database_disconnect_with_sanitized_log(
    monkeypatch, caplog, tmp_path: Path
) -> None:
    def unavailable(*_args, **_kwargs):
        raise RuntimeError(_SENTINEL)

    monkeypatch.setattr(proxy_main, "repository_gateway", unavailable)
    caplog.set_level(logging.WARNING)
    client = TestClient(
        create_app(settings=ProxySettings(db_path=tmp_path / "unavailable.sqlite3"))
    )

    response = client.post("/v1/chat/completions", json=_request())

    assert response.status_code == 200
    assert response.json()["object"] == "chat.completion"
    assert "proxy database initialization failed" in caplog.text
    assert _SENTINEL not in caplog.text
    assert _SENTINEL not in response.text


def test_control_api_disconnect_is_explicit_sanitized_503(monkeypatch) -> None:
    def unavailable():
        raise DatabaseOperationError("database operation failed")

    monkeypatch.setattr(api_main, "repository_gateway", unavailable)
    response = TestClient(api_main.app).get(
        "/clusters/data-pull-sql/routing"
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Routing state is unavailable"}
    assert _SENTINEL not in response.text
