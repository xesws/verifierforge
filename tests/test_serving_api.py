from __future__ import annotations

import base64
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import serving as serving_api
from core.serving_contracts import ServingState, ServingStatus


def _auth(value: str = "invite") -> dict[str, str]:
    token = base64.b64encode(f"judge:{value}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


class _Coordinator:
    def __init__(self) -> None:
        self.created = True
        self.wakes = 0

    def request_wake(self, model_id: str):
        self.wakes += 1
        return (
            ServingStatus(
                session_id="sv-test",
                model_id=model_id,
                state=ServingState.PROVISIONING,
                detail="capacity reserved",
                updated_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            ),
            self.created,
        )

    def status(self, model_id=None):
        return ServingStatus(
            model_id=model_id or "vf-demo",
            state=ServingState.COLD,
            detail="No serving session is active",
        )


def _client(monkeypatch):
    fake = _Coordinator()
    monkeypatch.setenv("VF_REVIEW_INVITE_CODE", "invite")
    monkeypatch.setattr(serving_api, "_COORDINATOR", fake)
    app = FastAPI()
    app.include_router(serving_api.router)
    return TestClient(app), fake


def test_wake_requires_invitation_and_explicit_spend_confirmation(monkeypatch) -> None:
    client, fake = _client(monkeypatch)
    assert client.post(
        "/serving/wake",
        json={"model_id": "vf-demo", "confirm_provider_spend": True},
    ).status_code == 401
    assert client.post(
        "/serving/wake",
        headers=_auth(),
        json={"model_id": "vf-demo", "confirm_provider_spend": False},
    ).status_code == 422
    response = client.post(
        "/serving/wake",
        headers=_auth(),
        json={"model_id": "vf-demo", "confirm_provider_spend": True},
    )
    assert response.status_code == 202
    assert response.json()["state"] == "provisioning"
    assert fake.wakes == 1


def test_idempotent_wake_is_200_and_status_is_contract_shaped(monkeypatch) -> None:
    client, fake = _client(monkeypatch)
    fake.created = False
    response = client.post(
        "/serving/wake",
        headers=_auth(),
        json={"model_id": "vf-demo", "confirm_provider_spend": True},
    )
    assert response.status_code == 200
    status = client.get("/serving/status", headers=_auth())
    assert status.status_code == 200
    assert status.json()["state"] == "cold"
    assert status.json()["url"] is None


def test_reviewer_parent_owns_serving_reaper_startup(monkeypatch) -> None:
    monkeypatch.setenv("VF_REVIEW_INVITE_CODE", "invite")
    from app.reviewer import main as reviewer_main

    reviewer = reviewer_main.create_app()
    assert reviewer_main.start_serving_reaper in reviewer.router.on_startup
