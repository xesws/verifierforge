"""Structural protocols for provisioning adapters and stores."""

from __future__ import annotations

from typing import Protocol

from core.provisioning_contracts import (
    ProvisionAuditEvent,
    ProvisionHandle,
    ProvisionSpec,
    ProvisionStatus,
)


class Provisioner(Protocol):
    async def provision(self, spec: ProvisionSpec) -> ProvisionHandle:
        """Create a provider-side resource and return its handle."""

    async def status(self, handle: ProvisionHandle) -> ProvisionStatus:
        """Return the provider-side status for a handle."""

    async def terminate(self, handle: ProvisionHandle) -> None:
        """Terminate a provider-side resource."""

    async def list_active(self) -> list[ProvisionHandle]:
        """Return provider-side handles that may still be billing."""


class ProvisionAuditLog(Protocol):
    async def append(self, event: ProvisionAuditEvent) -> None:
        """Persist one append-only audit event."""


class ActiveProvisionRegistry(Protocol):
    async def is_active(self, handle: ProvisionHandle) -> bool:
        """Return whether the durable DB still owns this active handle."""
