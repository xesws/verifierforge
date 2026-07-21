from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import app.api.provisioning as provisioning_api
from app.api.main import app
from app.db import repository_gateway
from app.db.records import AgentDecisionRecord, ApprovalRecord
from app.db.settings import DatabaseSettings
from core.agent_contracts import TrainingConfig
from core.p4_contracts import ForgeExecutionStatus


def _gateway(path: Path):
    return repository_gateway(DatabaseSettings.sqlite(path))


def _seed(gateway, *, approval_id: str = "approval-p4", owner: str = "owner") -> None:
    now = datetime.now(timezone.utc)

    async def write(repositories):
        await repositories.agent_decisions.put(
            AgentDecisionRecord(
                id="decision-p4",
                cluster_id="data-pull-sql",
                decision="forge",
                rationale="bounded mock forge",
                confidence=1.0,
                config_json=TrainingConfig(
                    budget_usd_cap=4.0, provider_pref="runpod"
                ).model_dump(mode="json"),
                trace_s3_key="vf/agent-traces/p4.json",
                model_name="mock",
                created_at=now,
            )
        )
        await repositories.approvals.put(
            ApprovalRecord(
                id=approval_id,
                decision_id="decision-p4",
                approved_by=owner,
                approved_at=now,
            )
        )

    gateway.call(write)


def test_settings_put_encrypts_and_response_never_contains_plaintext(
    tmp_path: Path, monkeypatch
) -> None:
    gateway = _gateway(tmp_path / "settings.sqlite3")
    monkeypatch.setattr(provisioning_api, "repository_gateway", lambda: gateway)
    monkeypatch.setenv("VF_CRED_KEY", Fernet.generate_key().decode())
    plaintext = "runpod-fixture-secret"

    response = TestClient(app).put(
        "/settings/provider-credentials/runpod",
        json={"user_id": "owner", "api_key": plaintext},
    )
    status = TestClient(app).get(
        "/settings/provider-credentials/runpod?user_id=owner"
    )

    assert response.status_code == status.status_code == 200
    assert response.json()["source"] == status.json()["source"] == "stored"
    assert plaintext not in response.text + status.text
    record = gateway.call(
        lambda repositories: repositories.credentials.get_for_user_provider(
            "owner", "runpod"
        )
    )
    assert record is not None
    assert plaintext.encode() not in record.encrypted_key


def test_settings_reports_system_fallback_without_exposing_value(
    tmp_path: Path, monkeypatch
) -> None:
    gateway = _gateway(tmp_path / "fallback.sqlite3")
    monkeypatch.setattr(provisioning_api, "repository_gateway", lambda: gateway)
    monkeypatch.setenv("RUNPOD_API_KEY", "system-fixture-secret")

    response = TestClient(app).get(
        "/settings/provider-credentials/runpod?user_id=owner"
    )

    assert response.status_code == 200
    assert response.json()["source"] == "system_env"
    assert response.json()["credential_id"] is None
    assert "system-fixture-secret" not in response.text


def test_start_forge_is_hidden_while_flag_is_off(monkeypatch) -> None:
    def unexpected_gateway():
        raise AssertionError("disabled Start Forge must not reach persistence")

    monkeypatch.setattr(provisioning_api, "repository_gateway", unexpected_gateway)
    monkeypatch.setenv("VF_AGENT_ENABLED", "true")
    monkeypatch.delenv("VF_AUTOPROVISION", raising=False)

    response = TestClient(app).post(
        "/approvals/approval-p4/start-forge",
        json={"requested_by": "owner", "confirm_provider_spend": True},
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Start Forge is disabled because VF_AUTOPROVISION=false"
    }


def test_discover_injects_autoprovision_state_without_enabling_it_by_default(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VF_AGENT_ENABLED", "true")
    monkeypatch.delenv("VF_AUTOPROVISION", raising=False)
    disabled = TestClient(app).get("/discover")
    monkeypatch.setenv("VF_AUTOPROVISION", "true")
    enabled = TestClient(app).get("/discover")

    assert 'name="vf-autoprovision-enabled" content="false"' in disabled.text
    assert 'name="vf-autoprovision-enabled" content="true"' in enabled.text
    assert "Start Forge is the separate spend boundary" in enabled.text


def test_approval_then_separate_start_runs_complete_mock_lifecycle(
    tmp_path: Path, monkeypatch
) -> None:
    gateway = _gateway(tmp_path / "mock.sqlite3")
    _seed(gateway)
    monkeypatch.setattr(provisioning_api, "repository_gateway", lambda: gateway)
    monkeypatch.setenv("VF_AGENT_ENABLED", "true")
    monkeypatch.setenv("VF_AUTOPROVISION", "true")
    monkeypatch.setenv("VF_PROVISION_BINDING", "mock")
    monkeypatch.setenv("VF_PROVISION_SSH_PUBLIC_KEY", "ssh-ed25519 " + "a" * 48)
    monkeypatch.setenv("VF_PROVISION_SYSTEM_BUDGET_USD_CAP", "1")

    before = TestClient(app).get("/approvals/approval-p4/forge-execution")
    started = TestClient(app).post(
        "/approvals/approval-p4/start-forge",
        json={"requested_by": "owner", "confirm_provider_spend": True},
    )
    after = TestClient(app).get("/approvals/approval-p4/forge-execution")

    assert before.status_code == started.status_code == after.status_code == 200
    assert before.json()["state"] == "approved"
    assert started.json()["state"] == "provisioning"
    final = ForgeExecutionStatus.model_validate(after.json())
    assert final.state.value == "done"
    assert final.budget_usd_cap == 1.0
    assert final.provision_handle == "mock-0001"
    events = gateway.call(
        lambda repositories: repositories.provision_audit.list_for_approval(
            "approval-p4"
        )
    )
    assert events[0].action == "provision.requested"
    assert events[-1].status == "TERMINATED"


def test_start_requires_confirmed_literal_and_matching_approver(
    tmp_path: Path, monkeypatch
) -> None:
    gateway = _gateway(tmp_path / "confirm.sqlite3")
    _seed(gateway)
    monkeypatch.setattr(provisioning_api, "repository_gateway", lambda: gateway)
    monkeypatch.setenv("VF_AGENT_ENABLED", "true")
    monkeypatch.setenv("VF_AUTOPROVISION", "true")

    unconfirmed = TestClient(app).post(
        "/approvals/approval-p4/start-forge",
        json={"requested_by": "owner", "confirm_provider_spend": False},
    )
    wrong_owner = TestClient(app).post(
        "/approvals/approval-p4/start-forge",
        json={"requested_by": "stranger", "confirm_provider_spend": True},
    )

    assert unconfirmed.status_code == 422
    assert wrong_owner.status_code == 409
