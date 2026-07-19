"""Strict contracts for provider-neutral provisioning dry runs."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import math
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProvisionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProvisionProvider(str, Enum):
    RUNPOD = "runpod"
    NEBIUS = "nebius"


class GPUClass(str, Enum):
    SMALL_ADA = "small_ada"
    MID_AMPERE = "mid_ampere"
    H100 = "h100"


class ProvisionState(str, Enum):
    REQUESTED = "REQUESTED"
    PROVISIONING = "PROVISIONING"
    BOOTSTRAPPING = "BOOTSTRAPPING"
    RUNNING = "RUNNING"
    COLLECTING = "COLLECTING"
    TERMINATED = "TERMINATED"
    FAILED = "FAILED"


TERMINAL_PROVISION_STATES = frozenset(
    {ProvisionState.TERMINATED, ProvisionState.FAILED}
)

DEFAULT_GPU_MAPPINGS: dict[ProvisionProvider, dict[GPUClass, tuple[str, ...]]] = {
    ProvisionProvider.RUNPOD: {
        GPUClass.SMALL_ADA: (
            "NVIDIA RTX 2000 Ada Generation",
            "NVIDIA RTX 4000 SFF Ada Generation",
            "NVIDIA RTX 4000 Ada Generation",
            "NVIDIA L4",
        ),
        GPUClass.MID_AMPERE: ("NVIDIA A10", "NVIDIA A40"),
        GPUClass.H100: ("NVIDIA H100 PCIe",),
    },
    ProvisionProvider.NEBIUS: {
        GPUClass.SMALL_ADA: ("L4",),
        GPUClass.MID_AMPERE: ("A100 40GB",),
        GPUClass.H100: ("H100 80GB",),
    },
}

BLOCKED_GPU_MODEL_FRAGMENTS = (
    "blackwell",
    "b100",
    "b200",
    "gb200",
    "rtx 50",
    "rtx_50",
    "sm_120",
)
SECRET_ENV_KEY_FRAGMENTS = (
    "API_KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "PRIVATE_KEY",
    "ACCESS_KEY",
    "CREDENTIAL",
)

_ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_REGION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_identifier(value: str, field_name: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a stable non-secret identifier")
    return value


class ProvisionSpec(ProvisionModel):
    job_id: str = Field(min_length=1, max_length=128)
    approval_id: str = Field(min_length=1, max_length=128)
    requested_by: str = Field(min_length=1, max_length=128)
    provider: ProvisionProvider
    gpu_class: GPUClass
    image: str = Field(min_length=1, max_length=512)
    container_disk_gb: int = Field(ge=20, le=4096)
    region_pref: list[str] | None = Field(default=None, max_length=8)
    env: dict[str, str] = Field(default_factory=dict, max_length=64)
    ports: list[int] = Field(default_factory=list, max_length=16)
    ssh_pubkey: str = Field(min_length=20, max_length=8192)
    budget_usd_cap: float = Field(gt=0, le=1000)
    max_runtime_min: int = Field(ge=1, le=7 * 24 * 60)

    @field_validator("job_id", "approval_id", "requested_by")
    @classmethod
    def validate_ids(cls, value: str, info: Any) -> str:
        return _validate_identifier(value, info.field_name)

    @field_validator("gpu_class", mode="before")
    @classmethod
    def reject_blocked_gpu_classes(cls, value: object) -> object:
        if isinstance(value, str):
            lowered = value.lower().replace("-", "_")
            if any(fragment in lowered for fragment in BLOCKED_GPU_MODEL_FRAGMENTS):
                raise ValueError("Blackwell and sm_120 GPU models are blocked")
        return value

    @field_validator("region_pref")
    @classmethod
    def validate_regions(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        seen: set[str] = set()
        for region in value:
            if not _REGION_RE.fullmatch(region):
                raise ValueError("region_pref entries must be stable provider region IDs")
            if region in seen:
                raise ValueError("region_pref entries must be unique")
            seen.add(region)
        return value

    @field_validator("env")
    @classmethod
    def validate_env(cls, value: dict[str, str]) -> dict[str, str]:
        for key, env_value in value.items():
            if not _ENV_KEY_RE.fullmatch(key):
                raise ValueError(f"invalid environment variable name: {key}")
            if any(fragment in key for fragment in SECRET_ENV_KEY_FRAGMENTS):
                raise ValueError(f"secret-bearing environment key is not allowed: {key}")
            if len(env_value) > 4096:
                raise ValueError(f"environment variable value is too long: {key}")
        return value

    @field_validator("ports")
    @classmethod
    def validate_ports(cls, value: list[int]) -> list[int]:
        seen: set[int] = set()
        for port in value:
            if port < 1 or port > 65535:
                raise ValueError("ports must be in the TCP port range")
            if port in seen:
                raise ValueError("ports must be unique")
            seen.add(port)
        return value

    @field_validator("ssh_pubkey")
    @classmethod
    def validate_ssh_pubkey(cls, value: str) -> str:
        if not value.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")):
            raise ValueError("ssh_pubkey must be an OpenSSH public key")
        return value

    @model_validator(mode="after")
    def validate_finite_budget(self) -> "ProvisionSpec":
        if not math.isfinite(self.budget_usd_cap):
            raise ValueError("budget_usd_cap must be finite")
        return self


class ProvisionHandle(ProvisionModel):
    provider: ProvisionProvider
    external_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    approval_id: str = Field(min_length=1, max_length=128)
    region: str | None = Field(default=None, max_length=64)
    ssh: str | None = Field(default=None, max_length=512)
    labels: dict[str, str] = Field(default_factory=dict, max_length=32)
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator("external_id", "job_id", "approval_id")
    @classmethod
    def validate_ids(cls, value: str, info: Any) -> str:
        return _validate_identifier(value, info.field_name)


class ProvisionStatus(ProvisionModel):
    state: ProvisionState
    ssh: str | None = Field(default=None, max_length=512)
    cost_accrued_usd: float = Field(default=0.0, ge=0)
    uptime_min: int = Field(default=0, ge=0)
    detail: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_status(self) -> "ProvisionStatus":
        if not math.isfinite(self.cost_accrued_usd):
            raise ValueError("cost_accrued_usd must be finite")
        return self


class ProvisionAuditEvent(ProvisionModel):
    actor: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    approval_id: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=128)
    provider: ProvisionProvider | None = None
    external_id: str | None = Field(default=None, max_length=128)
    before_state: ProvisionState | None = None
    after_state: ProvisionState | None = None
    reason: str = Field(min_length=1, max_length=1000)
    created_at: datetime = Field(default_factory=_utc_now)
    detail: dict[str, Any] = Field(default_factory=dict, max_length=32)

    @field_validator("actor", "job_id", "approval_id")
    @classmethod
    def validate_ids(cls, value: str, info: Any) -> str:
        return _validate_identifier(value, info.field_name)
