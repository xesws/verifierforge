from __future__ import annotations

import base64
import importlib

from fastapi.testclient import TestClient


def _auth(code: str) -> dict[str, str]:
    token = base64.b64encode(f"judge:{code}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_full_reviewer_app_requires_invitation_and_mounts_api_proxy(monkeypatch) -> None:
    monkeypatch.setenv("VF_REVIEW_INVITE_CODE", "invite-fixture")
    monkeypatch.setenv("VF_API_DATA_MODE", "hybrid")
    monkeypatch.setenv("VF_PROXY_UPSTREAM", "fake")
    monkeypatch.setenv("VF_PROXY_TUNED_UPSTREAM", "fake-tuned")
    module = importlib.import_module("app.reviewer.main")
    reviewer = module.create_app()
    client = TestClient(reviewer)

    assert client.get("/healthz").status_code == 200
    assert client.get("/jobs").status_code == 401
    jobs = client.get("/jobs", headers=_auth("invite-fixture"))
    assert jobs.status_code == 200
    assert len(jobs.json()) == 2
    completion = client.post(
        "/proxy/v1/chat/completions",
        headers={**_auth("invite-fixture"), "Content-Type": "application/json"},
        json={
            "model": "vf-demo",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert completion.status_code == 200
    assert completion.json()["object"] == "chat.completion"


def test_reviewer_cors_preflight_bypasses_invitation_for_api_and_proxy(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VF_REVIEW_INVITE_CODE", "invite-fixture")
    monkeypatch.setenv(
        "VF_CORS_ORIGINS",
        "http://localhost:5173,https://verifierforge.vercel.app",
    )
    module = importlib.import_module("app.reviewer.main")
    client = TestClient(module.create_app())

    for path in ("/clusters", "/proxy/v1/chat/completions"):
        response = client.options(
            path,
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert client.get("/jobs").status_code == 401


def test_full_reviewer_app_rejects_missing_invite_configuration(monkeypatch) -> None:
    monkeypatch.delenv("VF_REVIEW_INVITE_CODE", raising=False)
    module = importlib.import_module("app.reviewer.main")
    try:
        module.create_app()
    except RuntimeError as error:
        assert str(error) == "VF_REVIEW_INVITE_CODE is required for the full reviewer app"
    else:  # pragma: no cover - assertion branch
        raise AssertionError("missing invitation must fail closed")
