"""P-4 product boundary between an approval and provider provisioning."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import time
from typing import Mapping

from app.db import CredentialCipher, CredentialService, RepositoryGateway
from app.db.records import JobRecord, ProvisionEventRecord
from app.provisioning.mock import MockAdapter
from app.provisioning.orchestrator import LifecycleOrchestrator
from app.provisioning.policy import ProvisioningPolicy
from app.provisioning.runpod import MANAGED_NAME_PREFIX, RUNPOD_IMAGE, RunPodAdapter
from app.provisioning.termination import (
    DeletionReceipt,
    audit_deletion,
    confirm_deleted,
    schedule_billing,
)
from core.agent_contracts import AgentDecisionType, ProviderPreference, TrainingConfig
from core.p4_contracts import CredentialSource, ForgeExecutionStatus, ForgeLifecycle
from core.provisioning_contracts import (
    GPUClass,
    ProvisionAuditEvent,
    ProvisionProvider,
    ProvisionSpec,
    ProvisionState,
)
from uuid import uuid4


class ForgeExecutionError(RuntimeError):
    """Stable product execution failure that never exposes a credential."""


@dataclass(frozen=True)
class GatewayAuditLog:
    """Route async lifecycle audit calls through the gateway-owned DB loop."""

    gateway: RepositoryGateway

    async def append(self, event: ProvisionAuditEvent) -> None:
        provider = event.provider.value if event.provider is not None else "unknown"
        status = (
            event.after_state.value
            if event.after_state is not None
            else event.before_state.value
            if event.before_state is not None
            else "UNKNOWN"
        )
        record = ProvisionEventRecord(
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
                "before_state": event.before_state.value if event.before_state else None,
                "after_state": event.after_state.value if event.after_state else None,
                "reason": event.reason,
                "detail": event.detail,
            },
        )
        self.gateway.call(lambda repositories: repositories.provision_audit.append(record))


@dataclass(frozen=True)
class PreparedForge:
    status: ForgeExecutionStatus
    config: TrainingConfig
    requested_by: str


@dataclass
class CredentialResolver:
    """Resolve plaintext afresh for each provider HTTP call; never retain it."""

    gateway: RepositoryGateway
    user_id: str
    provider: ProvisionProvider
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)

    def source(self) -> CredentialSource:
        record = self.gateway.call(
            lambda repositories: repositories.credentials.get_for_user_provider(
                self.user_id, self.provider.value
            )
        )
        if record is not None:
            return CredentialSource.STORED
        if self.provider is ProvisionProvider.RUNPOD and self.environ.get(
            "RUNPOD_API_KEY", ""
        ).strip():
            return CredentialSource.SYSTEM_ENV
        return CredentialSource.MISSING

    def __call__(self) -> str:
        record = self.gateway.call(
            lambda repositories: repositories.credentials.get_for_user_provider(
                self.user_id, self.provider.value
            )
        )
        if record is not None:
            cipher = CredentialCipher.from_env(self.environ)
            return cipher.decrypt(
                record.encrypted_key,
                expected_user_id=self.user_id,
                expected_provider=self.provider.value,
            )
        if self.provider is ProvisionProvider.RUNPOD:
            value = self.environ.get("RUNPOD_API_KEY", "").strip()
            if value:
                return value
        raise ForgeExecutionError(
            f"No {self.provider.value} provider credential is configured"
        )


def put_provider_credential(
    gateway: RepositoryGateway,
    *,
    user_id: str,
    provider: ProvisionProvider,
    api_key: str,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, datetime]:
    cipher = CredentialCipher.from_env(environ)
    created_at = datetime.now(timezone.utc)

    async def write(repositories):
        service = CredentialService(repositories.credentials, cipher)
        return await service.put(
            user_id=user_id,
            provider=provider.value,
            value=api_key,
        )

    return gateway.call(write), created_at


def credential_source(
    gateway: RepositoryGateway,
    *,
    user_id: str,
    provider: ProvisionProvider,
    environ: Mapping[str, str] | None = None,
) -> CredentialSource:
    return CredentialResolver(
        gateway=gateway,
        user_id=user_id,
        provider=provider,
        environ=os.environ if environ is None else environ,
    ).source()


def prepare_forge(
    gateway: RepositoryGateway,
    *,
    approval_id: str,
    requested_by: str,
    system_budget_cap: float,
) -> PreparedForge:
    if system_budget_cap <= 0:
        raise ForgeExecutionError("system provision budget must be positive")

    async def read(repositories):
        approval = await repositories.approvals.get(approval_id)
        if approval is None:
            return None, None, None
        decision = await repositories.agent_decisions.get(approval.decision_id)
        job = await repositories.jobs.get(_job_id(approval_id))
        return approval, decision, job

    approval, decision, job = gateway.call(read)
    if approval is None:
        raise ForgeExecutionError("Approval not found")
    if approval.approved_by != requested_by:
        raise ForgeExecutionError("Start Forge requester must match the approver")
    if decision is None or decision.decision != AgentDecisionType.FORGE.value:
        raise ForgeExecutionError("Approval does not reference a forge decision")
    if decision.trace_s3_key is None or decision.run_status != "completed":
        raise ForgeExecutionError("Forge decision is not backed by a completed audit trace")
    if decision.config_json is None:
        raise ForgeExecutionError("Forge decision has no training config")
    config = TrainingConfig.model_validate(decision.config_json)
    provider = _provider(config.provider_pref)
    budget = min(config.budget_usd_cap, system_budget_cap)
    if job is not None:
        return PreparedForge(
            status=_status_from_job(job),
            config=config,
            requested_by=requested_by,
        )
    now = datetime.now(timezone.utc)
    status = ForgeExecutionStatus(
        approval_id=approval.id,
        decision_id=decision.id,
        job_id=_job_id(approval.id),
        provider=provider,
        state=ForgeLifecycle.APPROVED,
        budget_usd_cap=budget,
        detail="approval recorded; provider execution requires Start Forge",
        created_at=now,
        updated_at=now,
    )
    _persist_status(gateway, status, config)
    return PreparedForge(status=status, config=config, requested_by=requested_by)


def reserve_start(gateway: RepositoryGateway, prepared: PreparedForge) -> PreparedForge:
    status = prepared.status
    if status.state is not ForgeLifecycle.APPROVED:
        return prepared
    execution_binding = f"execution:{status.job_id}"
    gateway.call(
        lambda repositories: repositories.approvals.bind_provision_handle(
            status.approval_id, execution_binding
        )
    )
    updated = status.model_copy(
        update={
            "state": ForgeLifecycle.PROVISIONING,
            "detail": "explicit Start Forge confirmation accepted",
            "updated_at": datetime.now(timezone.utc),
        }
    )
    _persist_status(gateway, updated, prepared.config)
    return PreparedForge(
        status=updated,
        config=prepared.config,
        requested_by=prepared.requested_by,
    )


def get_execution(gateway: RepositoryGateway, approval_id: str) -> ForgeExecutionStatus:
    job = gateway.call(lambda repositories: repositories.jobs.get(_job_id(approval_id)))
    if job is not None:
        return _status_from_job(job)

    async def read(repositories):
        approval = await repositories.approvals.get(approval_id)
        if approval is None:
            return None, None
        return approval, await repositories.agent_decisions.get(approval.decision_id)

    approval, decision = gateway.call(read)
    if approval is None or decision is None or decision.config_json is None:
        raise ForgeExecutionError("Approval not found")
    config = TrainingConfig.model_validate(decision.config_json)
    now = datetime.now(timezone.utc)
    return ForgeExecutionStatus(
        approval_id=approval.id,
        decision_id=decision.id,
        job_id=_job_id(approval.id),
        provider=_provider(config.provider_pref),
        state=ForgeLifecycle.APPROVED,
        budget_usd_cap=min(config.budget_usd_cap, _system_budget()),
        detail="approval recorded; provider execution requires Start Forge",
        created_at=approval.approved_at,
        updated_at=now,
    )


def execute_forge(
    gateway: RepositoryGateway,
    prepared: PreparedForge,
    *,
    binding: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ForgeExecutionStatus:
    """Run one bounded mock lifecycle or one create/readiness/delete live smoke."""

    values = os.environ if environ is None else environ
    selected = (binding or values.get("VF_PROVISION_BINDING", "mock")).strip().lower()
    if selected not in {"mock", "runpod"}:
        return _fail(gateway, prepared, "VF_PROVISION_BINDING must be mock or runpod")
    try:
        return asyncio.run(_execute(gateway, prepared, binding=selected, environ=values))
    except Exception:
        return _fail(gateway, prepared, "Forge execution failed; inspect sanitized audit events")


async def _execute(
    gateway: RepositoryGateway,
    prepared: PreparedForge,
    *,
    binding: str,
    environ: Mapping[str, str],
) -> ForgeExecutionStatus:
    status = prepared.status
    resolver = CredentialResolver(
        gateway=gateway,
        user_id=prepared.requested_by,
        provider=status.provider,
        environ=environ,
    )
    if binding == "mock":
        adapter = MockAdapter()
        source = CredentialSource.MISSING
    else:
        if status.provider is not ProvisionProvider.RUNPOD:
            raise ForgeExecutionError("NebiusAdapter is not implemented")
        source = resolver.source()
        if source is CredentialSource.MISSING:
            raise ForgeExecutionError("No RunPod provider credential is configured")
        adapter = RunPodAdapter(api_key_provider=resolver)

    orchestrator = LifecycleOrchestrator(
        adapter=adapter,
        audit_log=GatewayAuditLog(gateway),
        policy=ProvisioningPolicy(
            autoprovision_enabled=True,
            max_concurrent_active=1,
            max_ticks=20,
        ),
    )
    spec = training_config_to_spec(
        prepared.config,
        approval_id=status.approval_id,
        job_id=status.job_id,
        requested_by=prepared.requested_by,
        system_budget_cap=status.budget_usd_cap,
        environ=environ,
    )
    handle = None
    last = status.model_copy(update={"credential_source": source})
    try:
        handle = await orchestrator.request(spec)
        last = _advance(
            gateway,
            prepared,
            last,
            state=ForgeLifecycle.PROVISIONING,
            handle=handle.external_id,
            detail="provider handle allocated",
        )
        if binding == "mock":
            for _ in range(20):
                observed = await orchestrator.tick(handle)
                lifecycle = _product_state(observed.state)
                last = _advance(
                    gateway,
                    prepared,
                    last,
                    state=lifecycle,
                    handle=handle.external_id,
                    cost=observed.cost_accrued_usd,
                    detail=observed.detail,
                )
                if lifecycle in {ForgeLifecycle.DONE, ForgeLifecycle.FAILED}:
                    return last
            raise ForgeExecutionError("mock lifecycle did not terminate")

        poll_seconds = max(1.0, float(environ.get("VF_P4_POLL_SECONDS", "5")))
        deadline = time.monotonic() + max(
            30.0, float(environ.get("VF_P4_READY_TIMEOUT_SECONDS", "600"))
        )
        while time.monotonic() < deadline:
            observed = await orchestrator.tick(handle)
            last = _advance(
                gateway,
                prepared,
                last,
                state=(
                    ForgeLifecycle.RUNNING
                    if observed.ssh
                    else ForgeLifecycle.PROVISIONING
                ),
                handle=handle.external_id,
                cost=observed.cost_accrued_usd,
                detail="provider readiness confirmed" if observed.ssh else observed.detail,
            )
            if observed.state in {ProvisionState.FAILED, ProvisionState.TERMINATED}:
                raise ForgeExecutionError("provider did not reach readiness")
            if observed.ssh:
                break
            await asyncio.sleep(poll_seconds)
        else:
            raise ForgeExecutionError("provider readiness timed out")

        last = _advance(
            gateway,
            prepared,
            last,
            state=ForgeLifecycle.COLLECTING,
            handle=handle.external_id,
            detail="P-4 wiring smoke complete; terminating before training",
        )
        await orchestrator.terminate(
            handle,
            actor=prepared.requested_by,
            reason="P-4 minimal wiring smoke complete",
        )
        receipt = await _prove_absent(adapter, handle, environ=environ)
        await audit_deletion(GatewayAuditLog(gateway), handle, receipt)
        schedule_path = environ.get("VF_P4_BILLING_SCHEDULE", "").strip()
        if schedule_path:
            schedule_billing(Path(schedule_path), handle, receipt.checked_at)
        return _advance(
            gateway,
            prepared,
            last,
            state=ForgeLifecycle.DONE,
            handle=handle.external_id,
            detail="provider DELETE accepted; target absent; raw vf-auto-* inventory zero",
        )
    except Exception:
        if handle is not None:
            try:
                await orchestrator.terminate(
                    handle,
                    actor="system",
                    reason="P-4 fail-closed cleanup",
                )
                if binding == "runpod":
                    receipt = await _prove_absent(adapter, handle, environ=environ)
                    await audit_deletion(GatewayAuditLog(gateway), handle, receipt)
                    schedule_path = environ.get("VF_P4_BILLING_SCHEDULE", "").strip()
                    if schedule_path:
                        schedule_billing(Path(schedule_path), handle, receipt.checked_at)
            except Exception:
                pass
        raise
    finally:
        if isinstance(adapter, RunPodAdapter):
            await adapter.aclose()


def training_config_to_spec(
    config: TrainingConfig,
    *,
    approval_id: str,
    job_id: str,
    requested_by: str,
    system_budget_cap: float,
    environ: Mapping[str, str] | None = None,
) -> ProvisionSpec:
    values = os.environ if environ is None else environ
    provider = _provider(config.provider_pref)
    budget = min(config.budget_usd_cap, system_budget_cap)
    if budget <= 0:
        raise ForgeExecutionError("effective provision budget must be positive")
    return ProvisionSpec(
        job_id=job_id,
        approval_id=approval_id,
        requested_by=requested_by,
        provider=provider,
        gpu_class=GPUClass.SMALL_ADA,
        image=values.get("VF_PROVISION_IMAGE", RUNPOD_IMAGE),
        container_disk_gb=int(values.get("VF_PROVISION_CONTAINER_DISK_GB", "80")),
        region_pref=None,
        env={
            "VF_TRAINING_BASE_MODEL": config.base_model,
            "VF_TRAINING_STEPS": str(config.steps),
            "VF_TRAINING_K": str(config.k),
            "VF_CHECKPOINT_INTERVAL": str(config.checkpoint_interval),
        },
        ports=[],
        ssh_pubkey=_public_key(values),
        budget_usd_cap=budget,
        max_runtime_min=int(values.get("VF_PROVISION_MAX_RUNTIME_MIN", "15")),
    )


async def _prove_absent(
    adapter: RunPodAdapter,
    handle,
    *,
    environ: Mapping[str, str],
) -> DeletionReceipt:
    return await confirm_deleted(
        adapter,
        handle,
        timeout_s=max(
            30.0, float(environ.get("VF_P4_DELETE_TIMEOUT_SECONDS", "180"))
        ),
        poll_s=max(1.0, float(environ.get("VF_P4_POLL_SECONDS", "5"))),
    )


def _public_key(environ: Mapping[str, str]) -> str:
    explicit = environ.get("VF_PROVISION_SSH_PUBLIC_KEY", "").strip()
    if explicit:
        return explicit
    path = Path(environ.get("VF_PROVISION_SSH_PUBLIC_KEY_PATH", "~/.ssh/id_ed25519.pub")).expanduser()
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        raise ForgeExecutionError("provision SSH public key is unavailable") from None
    return value


def _provider(preference: ProviderPreference) -> ProvisionProvider:
    if preference in {ProviderPreference.AUTO, ProviderPreference.RUNPOD}:
        return ProvisionProvider.RUNPOD
    return ProvisionProvider.NEBIUS


def _system_budget() -> float:
    try:
        return float(os.environ.get("VF_PROVISION_SYSTEM_BUDGET_USD_CAP", "5"))
    except ValueError:
        raise ForgeExecutionError("VF_PROVISION_SYSTEM_BUDGET_USD_CAP must be numeric") from None


def _job_id(approval_id: str) -> str:
    return f"forge-{approval_id[:48]}"


def _persist_status(
    gateway: RepositoryGateway,
    status: ForgeExecutionStatus,
    config: TrainingConfig,
) -> None:
    job_status = {
        ForgeLifecycle.APPROVED: "queued",
        ForgeLifecycle.PROVISIONING: "running",
        ForgeLifecycle.RUNNING: "running",
        ForgeLifecycle.COLLECTING: "running",
        ForgeLifecycle.DONE: "done",
        ForgeLifecycle.FAILED: "failed",
    }[status.state]
    gateway.call(
        lambda repositories: repositories.jobs.put(
            JobRecord(
                job_id=status.job_id,
                template="agent-forge",
                status=job_status,
                config_json=config.model_dump(mode="json"),
                created_at=status.created_at,
                summary_json={"p4_execution": status.model_dump(mode="json")},
            )
        )
    )


def _status_from_job(job: JobRecord) -> ForgeExecutionStatus:
    try:
        return ForgeExecutionStatus.model_validate(job.summary_json["p4_execution"])
    except (KeyError, TypeError, ValueError):
        raise ForgeExecutionError("Forge execution status is unavailable") from None


def _advance(
    gateway: RepositoryGateway,
    prepared: PreparedForge,
    previous: ForgeExecutionStatus,
    *,
    state: ForgeLifecycle,
    detail: str,
    handle: str | None = None,
    cost: float | None = None,
) -> ForgeExecutionStatus:
    status = previous.model_copy(
        update={
            "state": state,
            "detail": detail,
            "provision_handle": handle or previous.provision_handle,
            "cost_accrued_usd": (
                previous.cost_accrued_usd if cost is None else cost
            ),
            "updated_at": datetime.now(timezone.utc),
        }
    )
    _persist_status(gateway, status, prepared.config)
    return status


def _fail(
    gateway: RepositoryGateway,
    prepared: PreparedForge,
    detail: str,
) -> ForgeExecutionStatus:
    return _advance(
        gateway,
        prepared,
        prepared.status,
        state=ForgeLifecycle.FAILED,
        detail=detail,
    )


def _product_state(state: ProvisionState) -> ForgeLifecycle:
    return {
        ProvisionState.REQUESTED: ForgeLifecycle.PROVISIONING,
        ProvisionState.PROVISIONING: ForgeLifecycle.PROVISIONING,
        ProvisionState.BOOTSTRAPPING: ForgeLifecycle.RUNNING,
        ProvisionState.RUNNING: ForgeLifecycle.RUNNING,
        ProvisionState.COLLECTING: ForgeLifecycle.COLLECTING,
        ProvisionState.TERMINATED: ForgeLifecycle.DONE,
        ProvisionState.FAILED: ForgeLifecycle.FAILED,
    }[state]


__all__ = [
    "CredentialResolver",
    "ForgeExecutionError",
    "PreparedForge",
    "credential_source",
    "execute_forge",
    "get_execution",
    "prepare_forge",
    "put_provider_credential",
    "reserve_start",
    "training_config_to_spec",
]
