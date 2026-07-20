from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.cors import (
    DEFAULT_LOCAL_ORIGINS,
    configure_cors,
    cors_origin_regex,
    cors_origins,
)
from app.api.main import app as real_app
from mock.server import app as mock_app


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/clusters",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )


def test_real_and_mock_allow_only_common_local_origins_by_default() -> None:
    for app in (real_app, mock_app):
        client = TestClient(app)
        for origin in DEFAULT_LOCAL_ORIGINS:
            response = _preflight(client, origin)
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == origin
            allowed_headers = response.headers["access-control-allow-headers"].lower()
            assert "authorization" in allowed_headers
            assert "content-type" in allowed_headers

        denied = _preflight(client, "https://untrusted.example")
        assert denied.status_code == 400
        assert "access-control-allow-origin" not in denied.headers


def test_environment_allowlist_replaces_local_defaults() -> None:
    origins = cors_origins(
        {"VF_CORS_ORIGINS": "https://frontend.example/, https://admin.example"}
    )
    assert origins == ["https://frontend.example", "https://admin.example"]

    app = FastAPI()
    configure_cors(app, {"VF_CORS_ORIGINS": ",".join(origins)})
    client = TestClient(app)
    assert _preflight(client, "https://frontend.example").status_code == 200
    assert _preflight(client, "http://localhost:3000").status_code == 400


def test_wildcard_requires_explicit_value() -> None:
    assert cors_origins({"VF_CORS_ORIGINS": "*"}) == ["*"]
    assert cors_origins({}) == list(DEFAULT_LOCAL_ORIGINS)

    with pytest.raises(
        ValueError, match="VF_CORS_ORIGINS wildcard must be the only value"
    ):
        cors_origins({"VF_CORS_ORIGINS": "*,https://frontend.example"})


def test_hosted_preview_regex_is_explicit_and_validated() -> None:
    expression = r"^https://[a-z0-9-]+\.vercel\.app$"
    app = FastAPI()
    configure_cors(app, {"VF_CORS_ORIGIN_REGEX": expression})
    client = TestClient(app)

    assert cors_origin_regex({"VF_CORS_ORIGIN_REGEX": expression}) == expression
    response = _preflight(client, "https://vf-preview-123.vercel.app")
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        "https://vf-preview-123.vercel.app"
    )
    with pytest.raises(ValueError, match="valid regular expression"):
        cors_origin_regex({"VF_CORS_ORIGIN_REGEX": "["})
