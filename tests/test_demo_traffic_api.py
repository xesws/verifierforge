from __future__ import annotations

import base64
import threading
import time
from typing import Any

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import app.api.demo_traffic as demo_traffic
from app.api.demo_traffic import DemoTrafficController, DemoTrafficRequest
from app.api.main import app
from app.proxy.clusters import SYSTEM_PROMPTS_BY_CLUSTER
from app.proxy.main import ProxySettings, create_app
from scripts.traffic_gen import TrafficStats


def _auth(code: str) -> dict[str, str]:
    token = base64.b64encode(f"judge:{code}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _requests() -> dict[str, list[dict[str, Any]]]:
    return {
        "support-ticket": [{"model": "vf-demo", "messages": []}],
        "invoice": [{"model": "vf-demo", "messages": []}],
        "data-pull-sql": [{"model": "vf-demo", "messages": []}],
    }


def _wait_for_completion(controller: DemoTrafficController) -> None:
    deadline = time.monotonic() + 2
    while controller.status().running and time.monotonic() < deadline:
        time.sleep(0.01)
    assert controller.status().running is False


def test_demo_routes_require_invitation_and_feature_flag(monkeypatch) -> None:
    monkeypatch.setenv("VF_REVIEW_INVITE_CODE", "invite-fixture")
    monkeypatch.delenv("VF_DEMO_TRAFFIC_ENABLED", raising=False)
    client = TestClient(app)

    assert client.get("/demo/traffic/status").status_code == 401
    disabled = client.get(
        "/demo/traffic/status", headers=_auth("invite-fixture")
    )
    assert disabled.status_code == 404
    assert disabled.json() == {
        "detail": "Demo traffic simulation is disabled because VF_DEMO_TRAFFIC_ENABLED=false"
    }


def test_demo_route_starts_and_reports_bounded_progress(monkeypatch) -> None:
    monkeypatch.setenv("VF_REVIEW_INVITE_CODE", "invite-fixture")
    monkeypatch.setenv("VF_DEMO_TRAFFIC_ENABLED", "true")
    controller = DemoTrafficController(
        request_builder=_requests,
        dispatcher=lambda _payload: JSONResponse({"ok": True}),
    )
    monkeypatch.setattr(demo_traffic, "_CONTROLLER", controller)
    client = TestClient(app)

    initial = client.get(
        "/demo/traffic/status", headers=_auth("invite-fixture")
    )
    assert initial.status_code == 200
    assert initial.json() == {
        "total": 200,
        "rate": 5.0,
        "sent": 0,
        "success": 0,
        "failed": 0,
        "running": False,
        "error": None,
    }

    started = client.post(
        "/demo/traffic",
        headers=_auth("invite-fixture"),
        json={"total": 3, "rate": 20},
    )
    assert started.status_code == 202
    assert started.json()["total"] == 3
    _wait_for_completion(controller)

    completed = client.get(
        "/demo/traffic/status", headers=_auth("invite-fixture")
    )
    assert completed.json() == {
        "total": 3,
        "rate": 20.0,
        "sent": 3,
        "success": 3,
        "failed": 0,
        "running": False,
        "error": None,
    }


def test_demo_route_validates_public_limits(monkeypatch) -> None:
    monkeypatch.setenv("VF_REVIEW_INVITE_CODE", "invite-fixture")
    monkeypatch.setenv("VF_DEMO_TRAFFIC_ENABLED", "true")
    client = TestClient(app)
    headers = _auth("invite-fixture")

    assert client.post("/demo/traffic", headers=headers, json={"total": 501}).status_code == 422
    assert client.post("/demo/traffic", headers=headers, json={"rate": 0}).status_code == 422
    assert client.post("/demo/traffic", headers=headers, json={"rate": 21}).status_code == 422


def test_controller_returns_active_task_instead_of_starting_another() -> None:
    entered = threading.Event()
    release = threading.Event()
    replay_calls = 0

    def replay(*_args, total: int, on_progress, **_kwargs) -> TrafficStats:
        nonlocal replay_calls
        replay_calls += 1
        on_progress(TrafficStats(sent=1, success=1))
        entered.set()
        assert release.wait(timeout=2)
        result = TrafficStats(sent=total, success=total)
        on_progress(result)
        return result

    controller = DemoTrafficController(request_builder=_requests, replay=replay)
    first, created = controller.start(DemoTrafficRequest(total=4, rate=5))
    assert created is True
    assert first.total == 4
    assert entered.wait(timeout=1)

    active, duplicate_created = controller.start(DemoTrafficRequest(total=2, rate=10))
    assert duplicate_created is False
    assert active.total == 4
    assert active.sent == 1
    assert active.running is True
    assert replay_calls == 1

    release.set()
    _wait_for_completion(controller)
    assert controller.status().sent == 4


def test_controller_clears_running_after_fatal_setup_error() -> None:
    def broken_requests() -> dict[str, list[dict[str, Any]]]:
        raise ValueError("fixture path and secret detail must not escape")

    controller = DemoTrafficController(request_builder=broken_requests)
    controller.start(DemoTrafficRequest(total=2, rate=5))
    _wait_for_completion(controller)

    status = controller.status()
    assert status.error == "Demo traffic task failed"
    assert "fixture" not in status.error


def test_in_process_dispatcher_uses_the_normal_proxy_pipeline(tmp_path) -> None:
    recorded = []

    def recorder(record, **_kwargs) -> bool:
        recorded.append(record)
        return True

    proxy = create_app(
        settings=ProxySettings(upstream="fake", db_path=tmp_path / "traffic.db"),
        recorder=recorder,
        canary_draw=lambda: 1.0,
    )
    response = proxy.state.dispatch_product_completion(
        {
            "model": "vf-demo",
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPTS_BY_CLUSTER["data-pull-sql"],
                },
                {"role": "user", "content": "Return one row"},
            ],
        }
    )

    assert response.status_code == 200
    assert response.headers["X-VerifierForge-Route"] == "default"
    assert len(recorded) == 1
    assert recorded[0].route_path == "default"
