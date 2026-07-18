"""Audit helpers for P-1 tests and zero-cost demos."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from app.db.contracts import ProvisionAuditStore
from app.db.records import ProvisionEventRecord
from core.provisioning_contracts import ProvisionAuditEvent

from app.provisioning.errors import ProvisionAuditError
from app.provisioning.protocols import ProvisionAuditLog


@dataclass
class InMemoryAuditLog:
    events: list[ProvisionAuditEvent] = field(default_factory=list)
    fail_on_actions: set[str] = field(default_factory=set)

    async def append(self, event: ProvisionAuditEvent) -> None:
        if event.action in self.fail_on_actions:
            raise ProvisionAuditError(f"audit append failed for {event.action}")
        self.events.append(event)


class DatabaseAuditLog:
    """Append provision mutations to the DB-1 audit repository."""

    def __init__(self, store: ProvisionAuditStore) -> None:
        self.store = store

    async def append(self, event: ProvisionAuditEvent) -> None:
        provider = event.provider.value if event.provider is not None else "unknown"
        status = (
            event.after_state.value
            if event.after_state is not None
            else event.before_state.value
            if event.before_state is not None
            else "UNKNOWN"
        )
        try:
            await self.store.append(
                ProvisionEventRecord(
                    id=uuid4().hex,
                    approval_id=event.approval_id,
                    job_id=event.job_id,
                    provider=provider,
                    action=event.action,
                    status=status,
                    actor=event.actor,
                    occurred_at=event.created_at,
                    detail_json={
                        "external_id": event.external_id,
                        "before_state": (
                            event.before_state.value if event.before_state else None
                        ),
                        "after_state": (
                            event.after_state.value if event.after_state else None
                        ),
                        "reason": event.reason,
                        "detail": event.detail,
                    },
                )
            )
        except Exception as error:
            raise ProvisionAuditError(
                f"audit append failed for {event.action}"
            ) from error


__all__ = ["DatabaseAuditLog", "InMemoryAuditLog", "ProvisionAuditLog"]
