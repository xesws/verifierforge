from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.proxy.clusters import SYSTEM_PROMPTS_BY_CLUSTER
from app.proxy.main import ProxySettings, create_app
from app.proxy.traffic import estimate_cost
from app.proxy.upstream import ForwardedResponse


def _request(*, system: str = "Extract support fields.", model: str = "vf-demo") -> dict[str, object]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "Customer asks for a refund on order 42."},
        ],
        "temperature": 0,
    }


def _settings(tmp_path: Path, *, upstream: str = "fake", pricing: Path | None = None) -> ProxySettings:
    return ProxySettings(
        upstream=upstream,
        db_path=tmp_path / "traffic.db",
        pricing_path=pricing or Path("config/proxy_pricing.json"),
    )


def test_fake_proxy_is_deterministic_and_records_openai_metadata(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings=settings))

    first = client.post("/v1/chat/completions", json=_request())
    second = client.post("/v1/chat/completions", json=_request())

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    payload = first.json()
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"]["content"].startswith("vf-fake-completion-")
    assert payload["usage"]["total_tokens"] == (
        payload["usage"]["prompt_tokens"] + payload["usage"]["completion_tokens"]
    )

    with sqlite3.connect(settings.db_path) as connection:
        rows = connection.execute(
            "SELECT prompt_hash, model, tokens_in, tokens_out, latency_ms, cost_usd FROM traffic_requests"
        ).fetchall()
    assert len(rows) == 2
    expected_hash = hashlib.sha256(b"Extract support fields.").hexdigest()
    assert rows[0][0] == expected_hash
    assert rows[0][1] == "vf-demo"
    assert rows[0][2:4] == (payload["usage"]["prompt_tokens"], payload["usage"]["completion_tokens"])
    assert rows[0][4] >= 0
    assert rows[0][5] == pytest.approx(
        estimate_cost("vf-demo", rows[0][2], rows[0][3], pricing_path=settings.pricing_path)
    )


def test_real_mode_uses_canonical_llm_environment_and_returns_upstream_shape(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def real_forwarder(request, *, base_url: str, api_key: str) -> ForwardedResponse:
        captured.update({"request": request, "base_url": base_url, "api_key": api_key})
        return ForwardedResponse(201, {"object": "chat.completion", "upstream": True})

    monkeypatch.setenv("VF_LLM_API_KEY", "proxy-test-key")
    monkeypatch.setenv("VF_LLM_BASE_URL", "https://compatible.example/v1")
    request = _request()
    client = TestClient(create_app(settings=_settings(tmp_path, upstream="real"), real_forwarder=real_forwarder))

    response = client.post("/v1/chat/completions", json=request)

    assert response.status_code == 201
    assert response.json() == {"object": "chat.completion", "upstream": True}
    assert captured == {
        "request": request,
        "base_url": "https://compatible.example/v1",
        "api_key": "proxy-test-key",
    }


def test_http_tuned_upstream_uses_only_its_dedicated_key(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def real_forwarder(request, *, base_url: str, api_key: str) -> ForwardedResponse:
        captured.update({"request": request, "base_url": base_url, "api_key": api_key})
        return ForwardedResponse(200, {"object": "chat.completion"})

    settings = ProxySettings(
        upstream="fake",
        tuned_upstream="https://tuned.example/v1",
        tuned_api_key="endpoint-only-key",
        tuned_model="served-step-350",
        db_path=tmp_path / "traffic.db",
    )
    monkeypatch.setattr("app.proxy.main.get_route", lambda *_args, **_kwargs: __import__("app.proxy.routing", fromlist=["RouteRecord"]).RouteRecord("support-ticket-extraction", True, 100, "tuned"))
    client = TestClient(
        create_app(settings=settings, real_forwarder=real_forwarder, canary_draw=lambda: 0.0)
    )

    assert client.post(
        "/v1/chat/completions",
        json=_request(system=SYSTEM_PROMPTS_BY_CLUSTER["support-ticket-extraction"]),
    ).status_code == 200
    assert captured == {
        "request": {
            **_request(system=SYSTEM_PROMPTS_BY_CLUSTER["support-ticket-extraction"]),
            "model": "served-step-350",
        },
        "base_url": "https://tuned.example/v1",
        "api_key": "endpoint-only-key",
    }
    assert "endpoint-only-key" not in repr(settings)


def test_http_tuned_upstream_never_falls_back_to_llm_key(monkeypatch) -> None:
    monkeypatch.setenv("VF_PROXY_TUNED_UPSTREAM", "https://tuned.example/v1")
    monkeypatch.setenv("VF_LLM_API_KEY", "wrong-key")
    monkeypatch.delenv("VF_PROXY_TUNED_API_KEY", raising=False)

    with pytest.raises(ValueError, match="VF_PROXY_TUNED_API_KEY"):
        ProxySettings.from_env()


def test_http_tuned_upstream_requires_environment_discovered_model(monkeypatch) -> None:
    monkeypatch.setenv("VF_PROXY_TUNED_UPSTREAM", "https://tuned.example/v1")
    monkeypatch.setenv("VF_PROXY_TUNED_API_KEY", "endpoint-key")
    monkeypatch.delenv("VF_PROXY_TUNED_MODEL", raising=False)
    monkeypatch.delenv("VF_ENDPOINT_MODEL", raising=False)

    with pytest.raises(ValueError, match="VF_PROXY_TUNED_MODEL or VF_ENDPOINT_MODEL"):
        ProxySettings.from_env()


def test_database_failure_never_blocks_fake_completion(tmp_path: Path) -> None:
    def broken_recorder(*_args, **_kwargs) -> bool:
        raise sqlite3.OperationalError("database is locked")

    client = TestClient(create_app(settings=_settings(tmp_path), recorder=broken_recorder))

    response = client.post("/v1/chat/completions", json=_request())

    assert response.status_code == 200
    assert response.json()["object"] == "chat.completion"
    assert not (tmp_path / "traffic.db").exists()


def test_proxy_rejects_missing_openai_model_or_messages(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=_settings(tmp_path)))

    assert client.post("/v1/chat/completions", json={"messages": []}).status_code == 400
    assert client.post("/v1/chat/completions", json={"model": "vf-demo"}).status_code == 400
