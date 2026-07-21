from __future__ import annotations

import base64
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import serving as serving_api
from app.proxy.main import TunedCompletionUnavailable, TunedCompletionUpstreamError
from app.proxy.upstream import ForwardedResponse
from app.serving.session import ServingControlError
from core.serving_contracts import ServingState, ServingStatus


def _auth(value: str = "invite") -> dict[str, str]:
    token = base64.b64encode(f"judge:{value}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


class _Coordinator:
    def __init__(self) -> None:
        self.created = True
        self.wakes = 0
        self.sleeps = 0
        self.sleep_error: ServingControlError | None = None

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

    def request_sleep(self, model_id: str):
        self.sleeps += 1
        if self.sleep_error is not None:
            raise self.sleep_error
        return ServingStatus(
            model_id=model_id,
            state=ServingState.COLD,
            detail="Provider deletion confirmed; managed inventory is zero",
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


def test_sleep_requires_invitation_and_returns_confirmed_cold(monkeypatch) -> None:
    client, fake = _client(monkeypatch)
    assert client.post(
        "/serving/sleep", json={"model_id": "vf-demo"}
    ).status_code == 401

    response = client.post(
        "/serving/sleep",
        headers=_auth(),
        json={"model_id": "vf-demo"},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "cold"
    assert response.json()["url"] is None
    assert fake.sleeps == 1


def test_sleep_exposes_stable_control_errors_without_provider_details(monkeypatch) -> None:
    client, fake = _client(monkeypatch)
    fake.sleep_error = ServingControlError(
        "Provider deletion could not be proven", code="termination_unproven"
    )

    response = client.post(
        "/serving/sleep",
        headers=_auth(),
        json={"model_id": "vf-demo"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Provider deletion could not be proven"}


def test_reviewer_parent_owns_serving_reaper_startup(monkeypatch) -> None:
    monkeypatch.setenv("VF_REVIEW_INVITE_CODE", "invite")
    from app.reviewer import main as reviewer_main

    reviewer = reviewer_main.create_app()
    assert reviewer_main.start_serving_reaper in reviewer.router.on_startup


def test_tuned_completion_requires_invitation_and_returns_full_response(
    monkeypatch,
) -> None:
    client, _ = _client(monkeypatch)
    observed = []

    def forward(request):
        observed.append(request)
        return ForwardedResponse(
            200,
            {
                "id": "chatcmpl-tuned-1",
                "object": "chat.completion",
                "model": "verifierforge-step-350",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "SELECT customer_id FROM orders LIMIT 5;",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 8,
                    "total_tokens": 20,
                },
            },
        )

    monkeypatch.setattr(serving_api, "forward_tuned_completion", forward)
    body = {
        "model": "vf-demo",
        "messages": [{"role": "user", "content": "Compile SQL"}],
    }
    assert client.post("/serving/tuned-completion", json=body).status_code == 401

    response = client.post(
        "/serving/tuned-completion",
        headers=_auth(),
        json=body,
    )
    assert response.status_code == 200
    assert response.headers["X-VerifierForge-Route"] == "tuned"
    assert response.json()["choices"][0]["message"]["content"].startswith("SELECT")
    assert response.json()["usage"]["total_tokens"] == 20
    assert observed == [body]


def test_tuned_completion_fails_closed_without_provider_details(monkeypatch) -> None:
    client, _ = _client(monkeypatch)

    def cold(_request):
        raise TunedCompletionUnavailable("Tuned endpoint is cold; wake it first")

    monkeypatch.setattr(serving_api, "forward_tuned_completion", cold)
    body = {"model": "vf-demo", "messages": []}
    cold_response = client.post(
        "/serving/tuned-completion", headers=_auth(), json=body
    )
    assert cold_response.status_code == 409
    assert cold_response.json() == {"detail": "Tuned endpoint is cold; wake it first"}

    def failed(_request):
        try:
            raise RuntimeError("provider body includes endpoint-secret")
        except RuntimeError as error:
            raise TunedCompletionUpstreamError(
                "Tuned endpoint did not return a completion"
            ) from error

    monkeypatch.setattr(serving_api, "forward_tuned_completion", failed)
    failure = client.post("/serving/tuned-completion", headers=_auth(), json=body)
    assert failure.status_code == 502
    assert failure.json() == {
        "detail": "Tuned endpoint did not return a completion"
    }
    assert "endpoint-secret" not in failure.text
