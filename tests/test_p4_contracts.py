from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from core.p4_contracts import (
    CredentialSource,
    ForgeExecutionStatus,
    ForgeLifecycle,
    ProviderCredentialRequest,
    ProviderCredentialStatus,
    StartForgeRequest,
)


def test_p4_contracts_round_trip_without_serializing_secret() -> None:
    credential = ProviderCredentialRequest(user_id="owner", api_key="secret-value")
    assert "secret-value" not in repr(credential)
    assert credential.model_dump(mode="json")["api_key"] == "**********"

    status = ForgeExecutionStatus(
        approval_id="approval-1",
        decision_id="decision-1",
        job_id="forge-approval-1",
        provider="runpod",
        state=ForgeLifecycle.APPROVED,
        budget_usd_cap=1.0,
        credential_source=CredentialSource.STORED,
        created_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    assert ForgeExecutionStatus.model_validate_json(status.model_dump_json()) == status


def test_start_forge_requires_literal_second_confirmation() -> None:
    with pytest.raises(ValidationError):
        StartForgeRequest(requested_by="owner", confirm_provider_spend=False)


def test_credential_status_never_has_a_key_field() -> None:
    schema = ProviderCredentialStatus.model_json_schema()
    assert "api_key" not in schema["properties"]
