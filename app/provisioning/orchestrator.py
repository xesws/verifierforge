"""Provider-neutral lifecycle state machine and safety fuses."""

from __future__ import annotations

from dataclasses import dataclass

from core.provisioning_contracts import (
    ProvisionAuditEvent,
    ProvisionHandle,
    ProvisionSpec,
    ProvisionState,
    ProvisionStatus,
    TERMINAL_PROVISION_STATES,
)

from app.provisioning.errors import (
    ProvisionAuditError,
    ProvisioningError,
    ProvisionLifecycleError,
    ProvisionRejected,
)
from app.provisioning.policy import KillSwitch, ProvisioningPolicy
from app.provisioning.protocols import (
    ActiveProvisionRegistry,
    ProvisionAuditLog,
    Provisioner,
)


_ALLOWED_TRANSITIONS: dict[ProvisionState, set[ProvisionState]] = {
    ProvisionState.REQUESTED: {
        ProvisionState.PROVISIONING,
        ProvisionState.FAILED,
        ProvisionState.TERMINATED,
    },
    ProvisionState.PROVISIONING: {
        ProvisionState.PROVISIONING,
        ProvisionState.BOOTSTRAPPING,
        ProvisionState.FAILED,
        ProvisionState.TERMINATED,
    },
    ProvisionState.BOOTSTRAPPING: {
        ProvisionState.BOOTSTRAPPING,
        ProvisionState.RUNNING,
        ProvisionState.FAILED,
        ProvisionState.TERMINATED,
    },
    ProvisionState.RUNNING: {
        ProvisionState.RUNNING,
        ProvisionState.COLLECTING,
        ProvisionState.FAILED,
        ProvisionState.TERMINATED,
    },
    ProvisionState.COLLECTING: {
        ProvisionState.COLLECTING,
        ProvisionState.TERMINATED,
        ProvisionState.FAILED,
    },
    ProvisionState.TERMINATED: {ProvisionState.TERMINATED},
    ProvisionState.FAILED: {ProvisionState.FAILED},
}


@dataclass
class _ManagedProvision:
    spec: ProvisionSpec
    last_state: ProvisionState = ProvisionState.REQUESTED
    terminal_status: ProvisionStatus | None = None


class LifecycleOrchestrator:
    def __init__(
        self,
        *,
        adapter: Provisioner,
        audit_log: ProvisionAuditLog,
        policy: ProvisioningPolicy | None = None,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        if audit_log is None:
            raise ProvisionRejected("audit_log is required")
        self.adapter = adapter
        self.audit_log = audit_log
        self.policy = policy if policy is not None else ProvisioningPolicy.from_env()
        self.kill_switch = kill_switch if kill_switch is not None else KillSwitch()
        self._managed: dict[str, _ManagedProvision] = {}

    async def request(self, spec: ProvisionSpec) -> ProvisionHandle:
        if not self.policy.autoprovision_enabled:
            await self._append_audit(
                spec=spec,
                handle=None,
                action="provision.rejected",
                before_state=None,
                after_state=ProvisionState.FAILED,
                actor=spec.requested_by,
                reason="VF_AUTOPROVISION is disabled",
            )
            raise ProvisionRejected("VF_AUTOPROVISION is disabled")

        active = await self.adapter.list_active()
        if len(active) >= self.policy.max_concurrent_active:
            await self._append_audit(
                spec=spec,
                handle=None,
                action="provision.rejected",
                before_state=None,
                after_state=ProvisionState.FAILED,
                actor=spec.requested_by,
                reason="max concurrent active provision limit reached",
            )
            raise ProvisionRejected("max concurrent active provision limit reached")

        await self._append_audit(
            spec=spec,
            handle=None,
            action="provision.requested",
            before_state=None,
            after_state=ProvisionState.REQUESTED,
            actor=spec.requested_by,
            reason="approved provision request accepted",
        )

        try:
            handle = await self.adapter.provision(spec)
        except ProvisioningError:
            await self._append_audit(
                spec=spec,
                handle=None,
                action="provision.failed",
                before_state=ProvisionState.REQUESTED,
                after_state=ProvisionState.FAILED,
                actor="system",
                reason="provider did not allocate a handle",
            )
            raise
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            await self._append_audit(
                spec=spec,
                handle=None,
                action="provision.failed",
                before_state=ProvisionState.REQUESTED,
                after_state=ProvisionState.FAILED,
                actor="system",
                reason="provider failed before handle allocation",
            )
            raise ProvisionLifecycleError("provider failed before handle allocation") from exc

        try:
            await self._append_audit(
                spec=spec,
                handle=handle,
                action="provision.created",
                before_state=ProvisionState.REQUESTED,
                after_state=ProvisionState.PROVISIONING,
                actor="system",
                reason="provider handle allocated",
            )
        except ProvisionAuditError:
            await self.adapter.terminate(handle)
            raise

        self._managed[self._key(handle)] = _ManagedProvision(
            spec=spec, last_state=ProvisionState.PROVISIONING
        )
        return handle

    async def tick(self, handle: ProvisionHandle) -> ProvisionStatus:
        key = self._key(handle)
        managed = self._managed.get(key)
        if managed is None:
            raise ProvisionLifecycleError(f"unmanaged provision handle: {handle.external_id}")
        if managed.terminal_status is not None:
            return managed.terminal_status

        if self.kill_switch.active:
            return await self._terminate_and_mark(
                handle=handle,
                managed=managed,
                observed=None,
                terminal_state=ProvisionState.TERMINATED,
                actor="system",
                action="kill_switch.terminated",
                reason=self.kill_switch.reason,
            )

        observed = await self.adapter.status(handle)
        return await self.observe(handle, observed)

    async def observe(
        self, handle: ProvisionHandle, observed: ProvisionStatus
    ) -> ProvisionStatus:
        """Apply lifecycle/fuse policy to a provider or workload observation."""
        key = self._key(handle)
        managed = self._managed.get(key)
        if managed is None:
            raise ProvisionLifecycleError(f"unmanaged provision handle: {handle.external_id}")
        if managed.terminal_status is not None:
            return managed.terminal_status
        if self.kill_switch.active:
            return await self._terminate_and_mark(
                handle=handle,
                managed=managed,
                observed=observed,
                terminal_state=ProvisionState.TERMINATED,
                actor="system",
                action="kill_switch.terminated",
                reason=self.kill_switch.reason,
            )

        if not self._is_legal_transition(managed.last_state, observed.state):
            return await self._terminate_and_mark(
                handle=handle,
                managed=managed,
                observed=observed,
                terminal_state=ProvisionState.FAILED,
                actor="system",
                action="lifecycle.failed",
                reason=(
                    "illegal lifecycle transition "
                    f"{managed.last_state.value}->{observed.state.value}"
                ),
            )

        await self._record_observed_transition(handle, managed, observed)

        if observed.state == ProvisionState.BOOTSTRAPPING and observed.ssh is None:
            return await self._terminate_and_mark(
                handle=handle,
                managed=managed,
                observed=observed,
                terminal_state=ProvisionState.FAILED,
                actor="system",
                action="lifecycle.failed",
                reason="ssh unreachable during bootstrapping",
            )

        if observed.cost_accrued_usd >= managed.spec.budget_usd_cap:
            return await self._terminate_and_mark(
                handle=handle,
                managed=managed,
                observed=observed,
                terminal_state=ProvisionState.FAILED,
                actor="system",
                action="budget.terminated",
                reason="single job budget cap reached",
            )

        if observed.uptime_min >= managed.spec.max_runtime_min:
            return await self._terminate_and_mark(
                handle=handle,
                managed=managed,
                observed=observed,
                terminal_state=ProvisionState.FAILED,
                actor="system",
                action="runtime.terminated",
                reason="maximum runtime reached",
            )

        if observed.state == ProvisionState.FAILED:
            return await self._terminate_and_mark(
                handle=handle,
                managed=managed,
                observed=observed,
                terminal_state=ProvisionState.FAILED,
                actor="system",
                action="lifecycle.failed",
                reason=observed.detail or "provider reported failure",
            )

        if observed.state == ProvisionState.TERMINATED:
            managed.terminal_status = observed
            return observed

        return observed

    async def terminate(
        self,
        handle: ProvisionHandle,
        *,
        actor: str = "system",
        reason: str = "termination requested",
    ) -> ProvisionStatus:
        """Terminate one managed handle and durably close its lifecycle."""
        managed = self._managed.get(self._key(handle))
        if managed is None:
            raise ProvisionLifecycleError(f"unmanaged provision handle: {handle.external_id}")
        if managed.terminal_status is not None:
            return managed.terminal_status
        return await self._terminate_and_mark(
            handle=handle,
            managed=managed,
            observed=None,
            terminal_state=ProvisionState.TERMINATED,
            actor=actor,
            action="lifecycle.terminated",
            reason=reason,
        )

    async def run_to_completion(self, spec: ProvisionSpec) -> ProvisionStatus:
        try:
            handle = await self.request(spec)
        except ProvisioningError as exc:
            return ProvisionStatus(
                state=ProvisionState.FAILED,
                detail=str(exc),
            )

        status = ProvisionStatus(state=ProvisionState.REQUESTED, detail="requested")
        for _ in range(self.policy.max_ticks):
            status = await self.tick(handle)
            if status.state in TERMINAL_PROVISION_STATES:
                return status

        return await self._terminate_and_mark(
            handle=handle,
            managed=self._managed[self._key(handle)],
            observed=status,
            terminal_state=ProvisionState.FAILED,
            actor="system",
            action="lifecycle.failed",
            reason="orchestrator tick limit reached",
        )

    async def terminate_all(
        self, *, actor: str = "system", reason: str = "global termination requested"
    ) -> list[ProvisionHandle]:
        terminated: list[ProvisionHandle] = []
        for handle in await self.adapter.list_active():
            key = self._key(handle)
            managed = self._managed.get(key)
            await self.adapter.terminate(handle)
            if managed is not None:
                before = managed.last_state
                terminal = ProvisionStatus(
                    state=ProvisionState.TERMINATED,
                    detail=reason,
                )
                managed.terminal_status = terminal
                managed.last_state = ProvisionState.TERMINATED
                await self._append_audit(
                    spec=managed.spec,
                    handle=handle,
                    action="provision.terminated",
                    before_state=before,
                    after_state=ProvisionState.TERMINATED,
                    actor=actor,
                    reason=reason,
                )
            else:
                await self._append_handle_audit(
                    handle=handle,
                    action="provision.terminated",
                    before_state=None,
                    after_state=ProvisionState.TERMINATED,
                    actor=actor,
                    reason=reason,
                )
            terminated.append(handle)
        return terminated

    async def reap_orphans(
        self,
        registry: ActiveProvisionRegistry,
        *,
        actor: str = "system",
        reason: str = "provider active handle missing from durable registry",
    ) -> list[ProvisionHandle]:
        reaped: list[ProvisionHandle] = []
        for handle in await self.adapter.list_active():
            if await registry.is_active(handle):
                continue
            await self.adapter.terminate(handle)
            key = self._key(handle)
            managed = self._managed.get(key)
            if managed is not None:
                before = managed.last_state
                managed.terminal_status = ProvisionStatus(
                    state=ProvisionState.TERMINATED,
                    detail=reason,
                )
                managed.last_state = ProvisionState.TERMINATED
            else:
                before = None
            await self._append_handle_audit(
                handle=handle,
                action="orphan.reaped",
                before_state=before,
                after_state=ProvisionState.TERMINATED,
                actor=actor,
                reason=reason,
            )
            reaped.append(handle)
        return reaped

    async def _record_observed_transition(
        self,
        handle: ProvisionHandle,
        managed: _ManagedProvision,
        observed: ProvisionStatus,
    ) -> None:
        before = managed.last_state
        if before == observed.state:
            return
        try:
            await self._append_audit(
                spec=managed.spec,
                handle=handle,
                action="state.transition",
                before_state=before,
                after_state=observed.state,
                actor="system",
                reason=observed.detail or f"observed {observed.state.value}",
            )
        except ProvisionAuditError:
            await self.adapter.terminate(handle)
            raise
        managed.last_state = observed.state

    async def _terminate_and_mark(
        self,
        *,
        handle: ProvisionHandle,
        managed: _ManagedProvision,
        observed: ProvisionStatus | None,
        terminal_state: ProvisionState,
        actor: str,
        action: str,
        reason: str,
    ) -> ProvisionStatus:
        before = managed.last_state
        await self.adapter.terminate(handle)
        provider_stopped = ProvisionState.TERMINATED
        await self._append_audit(
            spec=managed.spec,
            handle=handle,
            action="provision.terminated",
            before_state=before,
            after_state=provider_stopped,
            actor=actor,
            reason=reason,
        )
        terminal = ProvisionStatus(
            state=terminal_state,
            ssh=None,
            cost_accrued_usd=observed.cost_accrued_usd if observed else 0.0,
            uptime_min=observed.uptime_min if observed else 0,
            detail=reason,
        )
        await self._append_audit(
            spec=managed.spec,
            handle=handle,
            action=action,
            before_state=before,
            after_state=terminal_state,
            actor=actor,
            reason=reason,
        )
        managed.last_state = terminal_state
        managed.terminal_status = terminal
        return terminal

    async def _append_audit(
        self,
        *,
        spec: ProvisionSpec,
        handle: ProvisionHandle | None,
        action: str,
        before_state: ProvisionState | None,
        after_state: ProvisionState | None,
        actor: str,
        reason: str,
    ) -> None:
        event = ProvisionAuditEvent(
            actor=actor,
            job_id=spec.job_id,
            approval_id=spec.approval_id,
            action=action,
            provider=handle.provider if handle else spec.provider,
            external_id=handle.external_id if handle else None,
            before_state=before_state,
            after_state=after_state,
            reason=reason,
        )
        try:
            await self.audit_log.append(event)
        except ProvisionAuditError:
            raise
        except Exception as exc:  # pragma: no cover - defensive store boundary
            raise ProvisionAuditError(f"audit append failed for {action}") from exc

    async def _append_handle_audit(
        self,
        *,
        handle: ProvisionHandle,
        action: str,
        before_state: ProvisionState | None,
        after_state: ProvisionState | None,
        actor: str,
        reason: str,
    ) -> None:
        event = ProvisionAuditEvent(
            actor=actor,
            job_id=handle.job_id,
            approval_id=handle.approval_id,
            action=action,
            provider=handle.provider,
            external_id=handle.external_id,
            before_state=before_state,
            after_state=after_state,
            reason=reason,
        )
        try:
            await self.audit_log.append(event)
        except ProvisionAuditError:
            raise
        except Exception as exc:  # pragma: no cover - defensive store boundary
            raise ProvisionAuditError(f"audit append failed for {action}") from exc

    @staticmethod
    def _is_legal_transition(before: ProvisionState, after: ProvisionState) -> bool:
        return after in _ALLOWED_TRANSITIONS[before]

    @staticmethod
    def _key(handle: ProvisionHandle) -> str:
        return f"{handle.provider.value}:{handle.external_id}"
