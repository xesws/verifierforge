"""Deterministic zero-cost mock provisioning adapter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.provisioning_contracts import (
    ProvisionHandle,
    ProvisionSpec,
    ProvisionState,
    ProvisionStatus,
)

from app.provisioning.errors import ProvisionCreateTimeout, ProvisionLifecycleError


class MockFailureMode(str, Enum):
    CREATE_TIMEOUT = "create_timeout"
    SSH_UNREACHABLE = "ssh_unreachable"
    MID_RUN_TERMINATION = "mid_run_termination"


_STATE_PATH = (
    ProvisionState.PROVISIONING,
    ProvisionState.BOOTSTRAPPING,
    ProvisionState.RUNNING,
    ProvisionState.COLLECTING,
    ProvisionState.TERMINATED,
)


@dataclass
class _MockInstance:
    spec: ProvisionSpec
    handle: ProvisionHandle
    poll_count: int = 0
    active: bool = True
    terminal_status: ProvisionStatus | None = None


class MockAdapter:
    """A stateful dry-run adapter with deterministic IDs and failures."""

    def __init__(
        self,
        *,
        failure_mode: MockFailureMode | None = None,
        cost_per_poll_usd: float = 0.05,
        uptime_per_poll_min: int = 1,
    ) -> None:
        self.failure_mode = failure_mode
        self.cost_per_poll_usd = cost_per_poll_usd
        self.uptime_per_poll_min = uptime_per_poll_min
        self._counter = 0
        self._instances: dict[str, _MockInstance] = {}

    async def provision(self, spec: ProvisionSpec) -> ProvisionHandle:
        if self.failure_mode == MockFailureMode.CREATE_TIMEOUT:
            raise ProvisionCreateTimeout("mock create timed out before handle allocation")
        self._counter += 1
        external_id = f"mock-{self._counter:04d}"
        region = spec.region_pref[0] if spec.region_pref else "mock-region-1"
        ssh = f"mock://{external_id}:22"
        handle = ProvisionHandle(
            provider=spec.provider,
            external_id=external_id,
            job_id=spec.job_id,
            approval_id=spec.approval_id,
            region=region,
            ssh=ssh,
            labels={"adapter": "mock", "job_id": spec.job_id},
        )
        self._instances[external_id] = _MockInstance(spec=spec, handle=handle)
        return handle

    async def status(self, handle: ProvisionHandle) -> ProvisionStatus:
        instance = self._lookup(handle)
        if instance.terminal_status is not None:
            return instance.terminal_status

        instance.poll_count += 1
        state = _STATE_PATH[min(instance.poll_count - 1, len(_STATE_PATH) - 1)]
        cost = round(instance.poll_count * self.cost_per_poll_usd, 6)
        uptime = instance.poll_count * self.uptime_per_poll_min

        if (
            self.failure_mode == MockFailureMode.SSH_UNREACHABLE
            and state == ProvisionState.BOOTSTRAPPING
        ):
            return ProvisionStatus(
                state=state,
                ssh=None,
                cost_accrued_usd=cost,
                uptime_min=uptime,
                detail="mock ssh endpoint unreachable",
            )

        if (
            self.failure_mode == MockFailureMode.MID_RUN_TERMINATION
            and state == ProvisionState.RUNNING
        ):
            instance.active = False
            instance.terminal_status = ProvisionStatus(
                state=ProvisionState.FAILED,
                ssh=None,
                cost_accrued_usd=cost,
                uptime_min=uptime,
                detail="mock provider reported mid-run termination",
            )
            return instance.terminal_status

        ssh = instance.handle.ssh if state in {
            ProvisionState.BOOTSTRAPPING,
            ProvisionState.RUNNING,
            ProvisionState.COLLECTING,
        } else None
        status = ProvisionStatus(
            state=state,
            ssh=ssh,
            cost_accrued_usd=cost,
            uptime_min=uptime,
            detail=f"mock {state.value.lower()}",
        )
        if state == ProvisionState.TERMINATED:
            instance.active = False
            instance.terminal_status = status
        return status

    async def terminate(self, handle: ProvisionHandle) -> None:
        instance = self._lookup(handle)
        instance.active = False
        instance.terminal_status = ProvisionStatus(
            state=ProvisionState.TERMINATED,
            ssh=None,
            cost_accrued_usd=round(instance.poll_count * self.cost_per_poll_usd, 6),
            uptime_min=instance.poll_count * self.uptime_per_poll_min,
            detail="mock terminated",
        )

    async def list_active(self) -> list[ProvisionHandle]:
        return [
            instance.handle
            for instance in self._instances.values()
            if instance.active and instance.terminal_status is None
        ]

    def _lookup(self, handle: ProvisionHandle) -> _MockInstance:
        try:
            return self._instances[handle.external_id]
        except KeyError as exc:
            raise ProvisionLifecycleError(
                f"unknown mock provision handle: {handle.external_id}"
            ) from exc
