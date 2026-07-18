"""Provisioning policy and kill-switch state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
import os


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ProvisioningPolicy:
    autoprovision_enabled: bool = False
    max_concurrent_active: int = 1
    max_ticks: int = 20

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ProvisioningPolicy":
        values = os.environ if environ is None else environ
        return cls(
            autoprovision_enabled=_env_bool(
                values.get("VF_AUTOPROVISION"), default=False
            ),
            max_concurrent_active=int(values.get("VF_PROVISION_MAX_CONCURRENT", "1")),
            max_ticks=int(values.get("VF_PROVISION_MAX_TICKS", "20")),
        )

    def __post_init__(self) -> None:
        if self.max_concurrent_active < 1:
            raise ValueError("max_concurrent_active must be >= 1")
        if self.max_ticks < 1:
            raise ValueError("max_ticks must be >= 1")


@dataclass
class KillSwitch:
    active: bool = False
    reason: str = "global kill switch"

    def activate(self, reason: str = "global kill switch") -> None:
        self.active = True
        self.reason = reason

    def reset(self) -> None:
        self.active = False
        self.reason = "global kill switch"
