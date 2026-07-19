"""Database ownership registry used by the conservative orphan reaper."""

from __future__ import annotations

from app.db.contracts import ApprovalStore, ProvisionAuditStore
from core.provisioning_contracts import ProvisionHandle


_TERMINAL_STATUSES = {"TERMINATED", "FAILED"}


class DatabaseActiveProvisionRegistry:
    """A handle is active only while its approval owns it and no terminal event exists."""

    def __init__(
        self, *, approvals: ApprovalStore, provision_audit: ProvisionAuditStore
    ) -> None:
        self.approvals = approvals
        self.provision_audit = provision_audit

    async def is_active(self, handle: ProvisionHandle) -> bool:
        approval = await self.approvals.get(handle.approval_id)
        if approval is None or approval.provision_handle != handle.external_id:
            return False
        events = await self.provision_audit.list_for_approval(handle.approval_id)
        return not any(
            event.status in _TERMINAL_STATUSES
            and event.detail_json.get("external_id") == handle.external_id
            for event in events
        )


__all__ = ["DatabaseActiveProvisionRegistry"]
