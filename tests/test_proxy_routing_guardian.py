from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from app.api.main import app as real_api
from app.proxy.clusters import SYSTEM_PROMPTS_BY_CLUSTER
from app.proxy.main import ProxySettings, create_app
from app.proxy.routing import RouteRecord, get_route, list_live_pass_rate, put_route, record_guardian_score
from app.proxy.traffic import TrafficRecord, record_traffic
from core.contracts import LivePassRate, RoutingState
from mock.server import app as mock_api


def _sql_request(prompt: str = "unknown SQL prompt") -> dict[str, object]:
    return {
        "model": "vf-demo",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPTS_BY_CLUSTER["data-pull-sql"]},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
    }


def _settings(tmp_path: Path, *, pool_path: Path | None = None) -> ProxySettings:
    return ProxySettings(
        db_path=tmp_path / "traffic.db",
        pricing_path=Path("config/proxy_pricing.json"),
        guardian_sample_rate=1.0,
        guardian_pool_path=pool_path or tmp_path / "missing-pool.jsonl",
    )


def test_canary_route_selects_tuned_fake_and_records_actual_route_path(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    put_route(RouteRecord("data-pull-sql", True, 50, "tuned"), db_path=settings.db_path)
    draws = iter((0.10, 0.90, 0.20, 0.80))
    client = TestClient(
        create_app(
            settings=settings,
            canary_draw=lambda: next(draws),
            guardian_draw=lambda: 1.0,
        )
    )

    responses = [client.post("/v1/chat/completions", json=_sql_request()) for _ in range(4)]

    assert [response.status_code for response in responses] == [200, 200, 200, 200]
    contents = [response.json()["choices"][0]["message"]["content"] for response in responses]
    assert contents[0].startswith("SELECT 'vf-fake-tuned-")
    assert contents[1].startswith("vf-fake-completion-")
    with sqlite3.connect(settings.db_path) as connection:
        paths = [
            row[0]
            for row in connection.execute(
                "SELECT route_taken FROM traffic_requests ORDER BY id"
            )
        ]
    assert paths == ["tuned", "default", "tuned", "default"]


def test_tuned_sql_guardian_uses_real_verifier_and_persists_rolling_point(tmp_path: Path) -> None:
    pool_path = tmp_path / "pool.jsonl"
    prompt = "Return the one user name."
    pool_path.write_text(
        json.dumps(
            {
                "prompt": prompt,
                "schema_sql": "CREATE TABLE users (name TEXT); INSERT INTO users VALUES ('Ada');",
                "expected_results": [["Ada"]],
                "reference_sql": "SELECT name FROM users",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    settings = _settings(tmp_path, pool_path=pool_path)
    put_route(RouteRecord("data-pull-sql", True, 100, "tuned"), db_path=settings.db_path)
    client = TestClient(
        create_app(
            settings=settings,
            canary_draw=lambda: 0.0,
            guardian_draw=lambda: 0.0,
            guardian_scheduler=lambda task: task(),
        )
    )

    response = client.post("/v1/chat/completions", json=_sql_request(prompt))

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "-- vf-fake-tuned\nSELECT name FROM users"
    points = list_live_pass_rate("data-pull-sql", db_path=settings.db_path)
    assert [(point.pass_rate, point.cluster_id) for point in points] == [(1.0, "data-pull-sql")]


def test_guardian_scheduler_failure_never_changes_completion_response(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    put_route(RouteRecord("data-pull-sql", True, 100, "tuned"), db_path=settings.db_path)

    def broken_scheduler(_task) -> None:
        raise RuntimeError("guardian queue unavailable")

    client = TestClient(
        create_app(
            settings=settings,
            canary_draw=lambda: 0.0,
            guardian_draw=lambda: 0.0,
            guardian_scheduler=broken_scheduler,
        )
    )

    response = client.post("/v1/chat/completions", json=_sql_request())

    assert response.status_code == 200
    assert response.json()["object"] == "chat.completion"


def test_route_storage_migrates_traffic_and_rolls_exact_pass_rate(tmp_path: Path) -> None:
    db_path = tmp_path / "traffic.db"
    assert record_traffic(
        TrafficRecord("2026-07-16T00:00:00Z", "hash", "vf-demo", 1, 1, 1.0, 0.0, "tuned"),
        db_path=db_path,
    )
    route = put_route(RouteRecord("data-pull-sql", True, 50, "tuned"), db_path=db_path)
    first = record_guardian_score("data-pull-sql", 1.0, db_path=db_path, timestamp="2026-07-16T00:00:00Z")
    second = record_guardian_score("data-pull-sql", 0.5, db_path=db_path, timestamp="2026-07-16T00:00:01Z")

    assert get_route("data-pull-sql", db_path=db_path) == route
    assert (first.pass_rate, second.pass_rate) == (1.0, 0.5)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT route_taken FROM traffic_requests"
        ).fetchone() == ("tuned",)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()


def test_mock_and_real_routing_and_live_rate_endpoints_validate_the_same_contracts(
    monkeypatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "traffic.db"
    monkeypatch.setenv("VF_PROXY_DB_PATH", str(db_path))
    payload = {
        "cluster_id": "data-pull-sql",
        "enabled": True,
        "canary_percent": 50,
        "target_model": "tuned",
    }
    real_client = TestClient(real_api)
    mock_client = TestClient(mock_api)

    real_put = real_client.put("/clusters/data-pull-sql/routing", json=payload)
    mock_put = mock_client.put("/clusters/data-pull-sql/routing", json=payload)

    assert real_put.status_code == mock_put.status_code == 200
    assert set(real_put.json()) == set(mock_put.json()) == {
        "cluster_id",
        "enabled",
        "canary_percent",
        "target_model",
    }
    assert RoutingState.model_validate(real_put.json()) == RoutingState.model_validate(mock_put.json())
    assert RoutingState.model_validate(real_client.get("/clusters/data-pull-sql/routing").json()) == RoutingState.model_validate(
        mock_client.get("/clusters/data-pull-sql/routing").json()
    )

    record_guardian_score("data-pull-sql", 1.0, db_path=db_path)
    real_live = real_client.get("/clusters/data-pull-sql/live-pass-rate")
    mock_live = mock_client.get("/clusters/data-pull-sql/live-pass-rate")

    assert real_live.status_code == mock_live.status_code == 200
    assert set(real_live.json()) == set(mock_live.json()) == {"cluster_id", "points"}
    assert LivePassRate.model_validate(real_live.json()).cluster_id == "data-pull-sql"
    assert LivePassRate.model_validate(mock_live.json()).cluster_id == "data-pull-sql"
    assert all(set(point) == {"timestamp", "pass_rate"} for point in real_live.json()["points"])
    assert all(set(point) == {"timestamp", "pass_rate"} for point in mock_live.json()["points"])
