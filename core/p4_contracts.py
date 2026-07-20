"""Additive product contracts for BYO credentials and Start Forge."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from core.provisioning_contracts import ProvisionProvider


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class P4Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CredentialSource(str, Enum):
    STORED = "stored"
    SYSTEM_ENV = "system_env"
    MISSING = "missing"


class ForgeLifecycle(str, Enum):
    APPROVED = "approved"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    COLLECTING = "collecting"
    DONE = "done"
    FAILED = "failed"


class ProviderCredentialRequest(P4Model):
    user_id: str = Field(min_length=1, max_length=128)
    api_key: SecretStr


class ProviderCredentialStatus(P4Model):
    user_id: str = Field(min_length=1, max_length=128)
    provider: ProvisionProvider
    configured: bool
    source: CredentialSource
    credential_id: str | None = Field(default=None, max_length=128)
    updated_at: datetime | None = None


class StartForgeRequest(P4Model):
    requested_by: str = Field(min_length=1, max_length=128)
    confirm_provider_spend: Literal[True]


class ForgeExecutionStatus(P4Model):
    approval_id: str = Field(min_length=1, max_length=128)
    decision_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    provider: ProvisionProvider
    state: ForgeLifecycle
    budget_usd_cap: float = Field(gt=0)
    cost_accrued_usd: float = Field(default=0.0, ge=0)
    provision_handle: str | None = Field(default=None, max_length=128)
    credential_source: CredentialSource | None = None
    detail: str = Field(default="", max_length=1000)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


__all__ = [
    "CredentialSource",
    "ForgeExecutionStatus",
    "ForgeLifecycle",
    "ProviderCredentialRequest",
    "ProviderCredentialStatus",
    "StartForgeRequest",
]
