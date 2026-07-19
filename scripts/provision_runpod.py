"""Execute the approved P2 RunPod lifecycle from local credentials only."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Any, Mapping

from dotenv import load_dotenv

from app.db import DatabaseSettings, create_database_runtime, create_repositories
from app.db.records import JobRecord
from app.provisioning import (
    DatabaseActiveProvisionRegistry,
    DatabaseAuditLog,
    LifecycleOrchestrator,
    ProvisioningPolicy,
    RunPodAdapter,
)
from app.provisioning.live import (
    P2_CONFIG_NAME,
    P2_MAX_RUNTIME_MIN,
    P2_TOTAL_STEPS,
    P2_WAVE_BUDGET_USD,
    S3RunCollector,
    validate_p2_config,
)
from app.provisioning.runpod import RUNPOD_IMAGE
from app.provisioning.termination import (
    BILLING_SLOTS,
    audit_deletion as _audit_deletion,
    confirm_deleted as _confirm_deleted,
    parse_timestamp as _parse_datetime,
    prefixed_pods as _prefixed_pods,
    reconcile_billing_schedule,
    schedule_billing as _schedule_billing,
)
from core.provisioning_contracts import (
    GPUClass,
    ProvisionAuditEvent,
    ProvisionHandle,
    ProvisionProvider,
    ProvisionSpec,
    ProvisionState,
    ProvisionStatus,
)
from scripts.s3_job_env import local_payload


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = ROOT / "runs" / "provisioning" / "v0.28.2"
MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"
TRAINING_POLL_SECONDS = 300
SSH_READY_TIMEOUT_SECONDS = 15 * 60
CLEANUP_SLA_SECONDS = 30 * 60


class LiveExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class FullRetryAdmission:
    previous_external_id: str
    root_external_id: str
    next_job_id: str
    prior_estimated_cost_usd: float


class EvidenceLedger:
    """Atomic local evidence with no credential-bearing fields."""

    def __init__(self, path: Path, *, approval_id: str, job_id: str) -> None:
        self.path = Path(path)
        self.payload: dict[str, Any] = {
            "schema_version": 1,
            "approval_id": approval_id,
            "job_id": job_id,
            "started_at": _now(),
            "events": [],
        }
        self._write()

    def event(self, action: str, **detail: Any) -> None:
        self.payload["events"].append(
            {"timestamp": _now(), "action": action, **detail}
        )
        self._write()

    def finish(self, *, status: str, **detail: Any) -> None:
        self.payload.update({"status": status, "finished_at": _now(), **detail})
        self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


async def execute_live(
    approval_id: str,
    *,
    poll_seconds: int,
    resume_gold_evidence: Path | None = None,
    resume_full_evidence: Path | None = None,
) -> dict[str, Any]:
    if resume_gold_evidence is not None and resume_full_evidence is not None:
        raise LiveExecutionError("gold resume and full-run retry are mutually exclusive")
    _require_local_environment()
    settings = DatabaseSettings.from_env()
    runtime = create_database_runtime(settings)
    repositories = create_repositories(runtime)
    adapter = RunPodAdapter(os.environ["RUNPOD_API_KEY"])
    audit = DatabaseAuditLog(repositories.provision_audit)
    policy = ProvisioningPolicy(
        autoprovision_enabled=True,
        max_concurrent_active=1,
        max_ticks=10_000,
    )
    orchestrator = LifecycleOrchestrator(adapter=adapter, audit_log=audit, policy=policy)
    wave_estimated_cost = 0.0
    try:
        approval = await repositories.approvals.get(approval_id)
        if approval is None:
            raise LiveExecutionError("approval does not exist")
        if resume_full_evidence is None and approval.provision_handle is not None:
            raise LiveExecutionError("approval is already bound to a provision handle")
        if resume_full_evidence is not None and approval.provision_handle is None:
            raise LiveExecutionError("full-run retry requires an approval bound to the failed handle")
        decision = await repositories.agent_decisions.get(approval.decision_id)
        if decision is None or decision.decision != "forge" or decision.config_json is None:
            raise LiveExecutionError("approval does not reference a persisted forge decision")
        config = validate_p2_config(decision.config_json)
        base_job_id = f"p2-{approval.id[:20]}"
        job_id = (
            _next_retry_job_id(approval.id, resume_full_evidence)
            if resume_full_evidence is not None
            else base_job_id
        )
        evidence = EvidenceLedger(
            EVIDENCE_ROOT / job_id / f"lifecycle-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json",
            approval_id=approval.id,
            job_id=job_id,
        )
        billing_schedule = evidence.path.parent / "billing-schedule.json"
        inventory = await adapter.list_account_pods()
        prefixed = _prefixed_pods(inventory)
        evidence.event(
            "account.inventory",
            pod_count=len(inventory),
            vf_auto_prefix_count=len(prefixed),
        )
        if prefixed:
            raise LiveExecutionError(
                f"raw RunPod inventory contains {len(prefixed)} vf-auto-* resource(s)"
            )

        public_key = _public_key()
        replacement_for_external_id: str | None = None
        retry_root_external_id: str | None = None
        if resume_full_evidence is not None:
            admission = await _resume_full_path(
                adapter=adapter,
                audit=audit,
                provision_audit=repositories.provision_audit,
                approval=approval,
                previous_path=resume_full_evidence,
                evidence=evidence,
            )
            if admission.next_job_id != job_id:
                raise LiveExecutionError("full-run retry job identity changed during admission")
            replacement_for_external_id = admission.previous_external_id
            retry_root_external_id = admission.root_external_id
            wave_estimated_cost = admission.prior_estimated_cost_usd
            _check_wave_budget(wave_estimated_cost)
            _assert_clean_s3_prefix(
                _s3_client(),
                bucket=os.environ["VF_S3_BUCKET"],
                prefix=os.environ.get("VF_S3_PREFIX", "vf"),
                job_id=job_id,
            )
            evidence.event("training.retry-prefix-empty", s3_job_id=job_id)
        elif resume_gold_evidence is not None:
            wave_estimated_cost += await _resume_gold_path(
                adapter=adapter,
                audit=audit,
                provision_audit=repositories.provision_audit,
                approval_id=approval.id,
                previous_path=resume_gold_evidence,
                evidence=evidence,
                billing_schedule=billing_schedule,
            )
        else:
            wave_estimated_cost += await _gold_path(
                adapter=adapter,
                orchestrator=orchestrator,
                audit=audit,
                approval_id=approval.id,
                public_key=public_key,
                evidence=evidence,
                billing_schedule=billing_schedule,
            )
        if resume_full_evidence is None:
            _check_wave_budget(wave_estimated_cost)
            wave_estimated_cost += await _orphan_probe(
                adapter=adapter,
                orchestrator=orchestrator,
                audit=audit,
                registry=DatabaseActiveProvisionRegistry(
                    approvals=repositories.approvals,
                    provision_audit=repositories.provision_audit,
                ),
                approval_id=approval.id,
                public_key=public_key,
                evidence=evidence,
                billing_schedule=billing_schedule,
            )
            _check_wave_budget(wave_estimated_cost)

        result = await _full_training(
            adapter=adapter,
            orchestrator=orchestrator,
            audit=audit,
            repositories=repositories,
            approval=approval,
            config=config,
            job_id=job_id,
            public_key=public_key,
            evidence=evidence,
            prior_estimated_cost=wave_estimated_cost,
            poll_seconds=poll_seconds,
            billing_schedule=billing_schedule,
            replacement_for_external_id=replacement_for_external_id,
            retry_root_external_id=retry_root_external_id,
        )
        wave_estimated_cost += float(result["estimated_cost_usd"])
        _check_wave_budget(wave_estimated_cost)
        evidence.finish(
            status="done",
            billing_status="pending",
            wave_estimated_cost_usd=round(wave_estimated_cost, 6),
            result=result,
        )
        return {
            **result,
            "billing_status": "pending",
            "wave_estimated_cost_usd": round(wave_estimated_cost, 6),
            "billing_schedule": str(billing_schedule),
            "evidence": str(evidence.path),
        }
    except BaseException as error:
        if "evidence" in locals():
            evidence.finish(
                status="failed",
                error_type=type(error).__name__,
                error=str(error)[:2000],
                billing_status="pending",
                wave_estimated_cost_usd=round(wave_estimated_cost, 6),
            )
        raise
    finally:
        await adapter.aclose()
        await runtime.close()


async def _resume_full_path(
    *,
    adapter: RunPodAdapter,
    audit: DatabaseAuditLog,
    provision_audit: Any,
    approval: Any,
    previous_path: Path,
    evidence: EvidenceLedger,
) -> FullRetryAdmission:
    """Admit one fail-closed replacement without rebinding its approval."""
    previous_path = Path(previous_path).expanduser().resolve()
    try:
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LiveExecutionError("full-run retry evidence is unreadable") from error
    base_job_id = f"p2-{approval.id[:20]}"
    previous_job_id = str(previous.get("job_id", ""))
    next_job_id = _next_retry_job_id(approval.id, previous_path)
    if previous.get("approval_id") != approval.id:
        raise LiveExecutionError("full-run retry evidence does not match the approval/job")
    events = previous.get("events")
    if not isinstance(events, list):
        raise LiveExecutionError("full-run retry evidence has no event list")

    is_initial_attempt = previous_job_id == base_job_id
    required = (
        (
            "gold.cleanup-admitted",
            "orphan.reaped",
            "training.created",
            "training.terminated",
        )
        if is_initial_attempt
        else (
            "training.retry-admitted",
            "training.retry-created",
            "training.terminated",
        )
    )
    selected: dict[str, dict[str, Any]] = {}
    for action in required:
        matches = [
            item for item in events
            if isinstance(item, dict) and item.get("action") == action
        ]
        if len(matches) != 1:
            raise LiveExecutionError(
                f"full-run retry evidence must contain exactly one {action!r} event"
            )
        selected[action] = matches[0]
    if any(
        isinstance(item, dict) and item.get("action") == "training.collected"
        for item in events
    ):
        raise LiveExecutionError("completed training evidence is not eligible for retry")

    creation_action = "training.created" if is_initial_attempt else "training.retry-created"
    external_id = str(selected[creation_action].get("external_id", ""))
    terminated = selected["training.terminated"]
    if not external_id or terminated.get("external_id") != external_id:
        raise LiveExecutionError("full-run retry provider identity is inconsistent")
    if is_initial_attempt:
        root_external_id = external_id
        carried_estimated_cost = 0.0
    else:
        retry_created = selected["training.retry-created"]
        root_external_id = str(
            retry_created.get("approval_root") or retry_created.get("retry_of") or ""
        )
        admitted = selected["training.retry-admitted"]
        if admitted.get("previous_external_id") != root_external_id:
            raise LiveExecutionError("full-run retry chain does not return to the bound root")
        try:
            carried_estimated_cost = float(admitted["prior_estimated_cost_usd"])
        except (KeyError, TypeError, ValueError) as error:
            raise LiveExecutionError("full-run retry chain lacks prior spend evidence") from error
    if not root_external_id or approval.provision_handle != root_external_id:
        raise LiveExecutionError("full-run retry provider identity is inconsistent")
    deletion = terminated.get("deletion")
    if not isinstance(deletion, dict) or not deletion.get("target_absent"):
        raise LiveExecutionError("full-run retry evidence lacks target-absent deletion proof")
    if deletion.get("vf_auto_prefix_count") != 0:
        raise LiveExecutionError("full-run retry evidence lacks prefix-zero deletion proof")

    audit_events = await provision_audit.list_for_approval(approval.id)
    matching_actions = {
        record.action
        for record in audit_events
        if record.detail_json.get("external_id") == external_id
    }
    if "provision.terminated" not in matching_actions:
        raise LiveExecutionError("full-run retry has no matching terminal audit")
    if "provider.deletion_confirmed" not in matching_actions:
        raise LiveExecutionError("full-run retry has no matching deletion-confirmed audit")

    created_at = _parse_datetime(str(previous.get("started_at", "")))
    deleted_at = _parse_datetime(str(deletion.get("checked_at", "")))
    elapsed_seconds = (deleted_at - created_at).total_seconds()
    if elapsed_seconds <= 0:
        raise LiveExecutionError("full-run retry evidence has an invalid elapsed interval")
    attempt_estimated_cost = (
        elapsed_seconds / (P2_MAX_RUNTIME_MIN * 60) * P2_WAVE_BUDGET_USD
    )
    prior_estimated_cost = carried_estimated_cost + attempt_estimated_cost
    handle = ProvisionHandle(
        provider=ProvisionProvider.RUNPOD,
        external_id=external_id,
        job_id=previous_job_id,
        approval_id=approval.id,
        created_at=created_at,
    )
    receipt = await _confirm_deleted(adapter, handle, timeout_s=0, poll_s=0)
    evidence.event(
        "training.retry-admitted",
        previous_external_id=external_id,
        previous_evidence=str(previous_path),
        deletion=asdict(receipt),
        elapsed_seconds=round(elapsed_seconds, 3),
        attempt_estimated_cost_usd=round(attempt_estimated_cost, 6),
        prior_estimated_cost_usd=round(prior_estimated_cost, 6),
    )
    await audit.append(
        ProvisionAuditEvent(
            actor="p2-executor",
            job_id=next_job_id,
            approval_id=approval.id,
            action="provision.retry-admitted",
            provider=ProvisionProvider.RUNPOD,
            external_id=external_id,
            before_state=ProvisionState.TERMINATED,
            after_state=ProvisionState.TERMINATED,
            reason="fail-closed export compatibility retry admitted from deletion proof",
            detail={
                "previous_job_id": previous_job_id,
                "root_external_id": root_external_id,
                "prior_estimated_cost_usd": round(prior_estimated_cost, 6),
            },
        )
    )
    return FullRetryAdmission(
        previous_external_id=external_id,
        root_external_id=root_external_id,
        next_job_id=next_job_id,
        prior_estimated_cost_usd=prior_estimated_cost,
    )


def _next_retry_job_id(approval_id: str, previous_path: Path) -> str:
    try:
        previous = json.loads(Path(previous_path).expanduser().resolve().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LiveExecutionError("full-run retry evidence is unreadable") from error
    if previous.get("approval_id") != approval_id:
        raise LiveExecutionError("full-run retry evidence does not match the approval/job")
    base_job_id = f"p2-{approval_id[:20]}"
    previous_job_id = str(previous.get("job_id", ""))
    if previous_job_id == base_job_id:
        return f"{base_job_id}-r2"
    prefix = f"{base_job_id}-r"
    suffix = previous_job_id.removeprefix(prefix)
    if not previous_job_id.startswith(prefix) or not suffix.isdigit() or int(suffix) < 2:
        raise LiveExecutionError("full-run retry evidence has an invalid attempt job ID")
    return f"{base_job_id}-r{int(suffix) + 1}"


async def _resume_gold_path(
    *,
    adapter: RunPodAdapter,
    audit: DatabaseAuditLog,
    provision_audit: Any,
    approval_id: str,
    previous_path: Path,
    evidence: EvidenceLedger,
    billing_schedule: Path,
) -> float:
    previous_path = Path(previous_path).expanduser().resolve()
    try:
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LiveExecutionError("resume gold evidence is unreadable") from error
    expected_job_id = f"p2-{approval_id[:20]}"
    if previous.get("approval_id") != approval_id or previous.get("job_id") != expected_job_id:
        raise LiveExecutionError("resume gold evidence does not match the approval/job")
    if previous.get("status") != "failed" or "billing receipt" not in str(previous.get("error", "")):
        raise LiveExecutionError("resume gold evidence is not the historical billing-delay failure")
    events = previous.get("events")
    if not isinstance(events, list):
        raise LiveExecutionError("resume gold evidence has no event list")
    if any(
        str(event.get("action", "")).startswith(("orphan.", "training."))
        for event in events
        if isinstance(event, dict)
    ):
        raise LiveExecutionError("resume gold evidence advanced beyond the gold stage")
    created = [
        event for event in events
        if isinstance(event, dict) and event.get("action") == "gold.created"
    ]
    ready = [
        event for event in events
        if isinstance(event, dict) and event.get("action") == "gold.ready"
    ]
    if len(created) != 1 or len(ready) != 1:
        raise LiveExecutionError("resume gold evidence must contain exactly one create and ready event")
    external_id = str(created[0].get("external_id", ""))
    if not external_id or ready[0].get("external_id") != external_id:
        raise LiveExecutionError("resume gold evidence has inconsistent provider identity")
    created_at = _parse_datetime(str(created[0].get("timestamp", "")))
    audit_events = await provision_audit.list_for_approval(approval_id)
    terminated = [
        record for record in audit_events
        if record.action in {"provision.terminated", "lifecycle.terminated"}
        and record.detail_json.get("external_id") == external_id
    ]
    if not terminated:
        raise LiveExecutionError("resume gold evidence has no matching DELETE audit")
    deleted_at = min(record.occurred_at for record in terminated)
    handle = ProvisionHandle(
        provider=ProvisionProvider.RUNPOD,
        external_id=external_id,
        job_id=f"p2-gold-{approval_id[:12]}",
        approval_id=approval_id,
        created_at=created_at,
    )
    receipt = await _confirm_deleted(adapter, handle, timeout_s=0, poll_s=0)
    estimated_cost = float(
        ready[0].get("estimated_cost_usd", ready[0].get("cost_accrued_usd", 0.0)) or 0.0
    )
    evidence.event(
        "gold.cleanup-admitted",
        external_id=external_id,
        previous_evidence=str(previous_path),
        historical_deleted_at=deleted_at.astimezone(timezone.utc).isoformat(),
        deletion=asdict(receipt),
        billing_status="pending",
        estimated_cost_usd=round(estimated_cost, 6),
    )
    await _audit_deletion(audit, handle, receipt)
    _schedule_billing(
        billing_schedule,
        handle,
        deleted_at.astimezone(timezone.utc).isoformat(),
    )
    return estimated_cost


async def _gold_path(
    *,
    adapter: RunPodAdapter,
    orchestrator: LifecycleOrchestrator,
    audit: DatabaseAuditLog,
    approval_id: str,
    public_key: str,
    evidence: EvidenceLedger,
    billing_schedule: Path,
) -> float:
    spec = _spec(
        job_id=f"p2-gold-{approval_id[:12]}",
        approval_id=approval_id,
        public_key=public_key,
        budget=P2_WAVE_BUDGET_USD,
        max_runtime=30,
    )
    handle: ProvisionHandle | None = None
    trigger = time.monotonic()
    estimated_cost: float | None = None
    try:
        handle = await orchestrator.request(spec)
        evidence.event("gold.created", external_id=handle.external_id)
        status = await _wait_for_ssh(adapter, orchestrator, handle, timeout_s=SSH_READY_TIMEOUT_SECONDS)
        estimated_cost = status.cost_accrued_usd
        evidence.event(
            "gold.ready",
            external_id=handle.external_id,
            estimated_cost_usd=estimated_cost,
        )
    finally:
        if handle is not None:
            await orchestrator.terminate(handle, reason="P2 gold-path teardown")
            receipt = await _confirm_deleted(adapter, handle)
            cleanup_seconds = round(time.monotonic() - trigger, 3)
            if cleanup_seconds > CLEANUP_SLA_SECONDS:
                raise LiveExecutionError("gold-path cleanup exceeded the 30-minute SLA")
            evidence.event(
                "gold.terminated",
                external_id=handle.external_id,
                cleanup_seconds=cleanup_seconds,
                deletion=asdict(receipt),
                billing_status="pending",
                estimated_cost_usd=round(estimated_cost or 0.0, 6),
            )
            await _audit_deletion(audit, handle, receipt)
            _schedule_billing(billing_schedule, handle, receipt.checked_at)
    if estimated_cost is None:
        raise LiveExecutionError("gold path failed before a RunPod handle was allocated")
    return estimated_cost


async def _orphan_probe(
    *,
    adapter: RunPodAdapter,
    orchestrator: LifecycleOrchestrator,
    audit: DatabaseAuditLog,
    registry: DatabaseActiveProvisionRegistry,
    approval_id: str,
    public_key: str,
    evidence: EvidenceLedger,
    billing_schedule: Path,
) -> float:
    spec = _spec(
        job_id=f"p2-orphan-{approval_id[:12]}",
        approval_id=approval_id,
        public_key=public_key,
        budget=P2_WAVE_BUDGET_USD,
        max_runtime=30,
    )
    handle: ProvisionHandle | None = None
    trigger = time.monotonic()
    estimated_cost = 0.0
    deletion_recorded = False
    try:
        handle = await adapter.provision(spec)
        await audit.append(
            ProvisionAuditEvent(
                actor="p2-orphan-probe",
                job_id=spec.job_id,
                approval_id=approval_id,
                action="provision.created",
                provider=ProvisionProvider.RUNPOD,
                external_id=handle.external_id,
                before_state=ProvisionState.REQUESTED,
                after_state=ProvisionState.PROVISIONING,
                reason="intentional unbound provider handle for orphan-reaper proof",
            )
        )
        evidence.event("orphan.created", external_id=handle.external_id)
        before_reap = await adapter.status(handle)
        estimated_cost = before_reap.cost_accrued_usd
        reaped = await orchestrator.reap_orphans(
            registry, actor="p2-orphan-probe", reason="intentional P2 orphan-reaper proof"
        )
        if [item.external_id for item in reaped] != [handle.external_id]:
            raise LiveExecutionError("orphan reaper did not terminate exactly its test resource")
        receipt = await _confirm_deleted(adapter, handle)
        cleanup_seconds = round(time.monotonic() - trigger, 3)
        if cleanup_seconds > CLEANUP_SLA_SECONDS:
            raise LiveExecutionError("orphan cleanup exceeded the 30-minute SLA")
        evidence.event(
            "orphan.reaped",
            external_id=handle.external_id,
            cleanup_seconds=cleanup_seconds,
            deletion=asdict(receipt),
            billing_status="pending",
            estimated_cost_usd=round(estimated_cost, 6),
        )
        await _audit_deletion(audit, handle, receipt)
        _schedule_billing(billing_schedule, handle, receipt.checked_at)
        deletion_recorded = True
        return estimated_cost
    except Exception:
        if handle is not None and not deletion_recorded:
            if await adapter.get_pod(handle.external_id) is not None:
                await adapter.terminate(handle)
            receipt = await _confirm_deleted(adapter, handle)
            await _audit_deletion(audit, handle, receipt)
            _schedule_billing(billing_schedule, handle, receipt.checked_at)
        raise


async def _full_training(
    *,
    adapter: RunPodAdapter,
    orchestrator: LifecycleOrchestrator,
    audit: DatabaseAuditLog,
    repositories: Any,
    approval: Any,
    config: Any,
    job_id: str,
    public_key: str,
    evidence: EvidenceLedger,
    prior_estimated_cost: float,
    poll_seconds: int,
    billing_schedule: Path,
    replacement_for_external_id: str | None = None,
    retry_root_external_id: str | None = None,
) -> dict[str, Any]:
    s3_prefix = os.environ.get("VF_S3_PREFIX", "vf").strip("/")
    job = JobRecord(
        job_id=job_id,
        template="nl2sql",
        status="queued",
        config_json=config.model_dump(mode="json"),
        created_at=datetime.now(timezone.utc),
        s3_prefix=f"{s3_prefix}/jobs/{job_id}",
        summary_json={
            "approval_id": approval.id,
            "profile": P2_CONFIG_NAME,
            **(
                {"retry_of": replacement_for_external_id}
                if replacement_for_external_id is not None
                else {}
            ),
        },
    )
    await repositories.jobs.put(job)
    spec = _spec(
        job_id=job_id,
        approval_id=approval.id,
        public_key=public_key,
        budget=min(
            float(config.budget_usd_cap),
            P2_WAVE_BUDGET_USD - prior_estimated_cost,
        ),
        max_runtime=P2_MAX_RUNTIME_MIN,
    )
    handle: ProvisionHandle | None = None
    cleanup_trigger: float | None = None
    start_monotonic = time.monotonic()
    completed = False
    estimated_cost = 0.0
    try:
        handle = await orchestrator.request(spec)
        if replacement_for_external_id is None:
            try:
                await repositories.approvals.bind_provision_handle(approval.id, handle.external_id)
            except Exception:
                cleanup_trigger = time.monotonic()
                raise
            evidence.event("training.created", external_id=handle.external_id)
        else:
            if (
                retry_root_external_id is None
                or approval.provision_handle != retry_root_external_id
            ):
                cleanup_trigger = time.monotonic()
                raise LiveExecutionError("retry approval binding changed before provider create")
            await audit.append(
                ProvisionAuditEvent(
                    actor="p2-executor",
                    job_id=job_id,
                    approval_id=approval.id,
                    action="provision.retry",
                    provider=ProvisionProvider.RUNPOD,
                    external_id=handle.external_id,
                    before_state=ProvisionState.PROVISIONING,
                    after_state=ProvisionState.PROVISIONING,
                    reason="replacement for a fail-closed checkpoint publication attempt",
                    detail={
                        "retry_of": replacement_for_external_id,
                        "approval_root": retry_root_external_id,
                    },
                )
            )
            evidence.event(
                "training.retry-created",
                external_id=handle.external_id,
                retry_of=replacement_for_external_id,
                approval_root=retry_root_external_id,
            )
        await repositories.jobs.put(_job_status(job, "running", {"external_id": handle.external_id}))
        ready = await _wait_for_ssh(adapter, orchestrator, handle, timeout_s=SSH_READY_TIMEOUT_SECONDS)
        estimated_cost = max(estimated_cost, ready.cost_accrued_usd)
        if ready.ssh is None:
            raise LiveExecutionError("RunPod did not expose SSH")
        revision = _prepare_and_bootstrap(ready.ssh, evidence=evidence)
        await orchestrator.observe(
            handle,
            ProvisionStatus(
                state=ProvisionState.RUNNING,
                ssh=ready.ssh,
                cost_accrued_usd=ready.cost_accrued_usd,
                uptime_min=ready.uptime_min,
                detail="P2 bootstrap complete and training launched",
            ),
        )
        _launch_s3_job(ready.ssh, job_id=job_id, config=P2_CONFIG_NAME)
        evidence.event("training.launched", revision=revision, s3_prefix=job.s3_prefix)

        collector = S3RunCollector(
            _s3_client(),
            bucket=os.environ["VF_S3_BUCKET"],
            prefix=s3_prefix,
            job_id=job_id,
        )
        while True:
            snapshot = collector.snapshot()
            if snapshot.failure_ready:
                cleanup_trigger = time.monotonic()
                raise LiveExecutionError(
                    "remote trainer published checkpoint-publication-failure evidence"
                )
            current = await adapter.status(handle)
            estimated_cost = max(estimated_cost, current.cost_accrued_usd)
            if current.state in {ProvisionState.FAILED, ProvisionState.TERMINATED}:
                cleanup_trigger = time.monotonic()
                raise LiveExecutionError(f"RunPod terminated during training: {current.detail}")
            if prior_estimated_cost + current.cost_accrued_usd >= P2_WAVE_BUDGET_USD:
                cleanup_trigger = time.monotonic()
                raise LiveExecutionError("P2 wave budget fuse reached")
            if current.uptime_min >= P2_MAX_RUNTIME_MIN:
                cleanup_trigger = time.monotonic()
                raise LiveExecutionError("P2 runtime fuse reached")
            await orchestrator.observe(
                handle,
                ProvisionStatus(
                    state=ProvisionState.RUNNING,
                    ssh=current.ssh,
                    cost_accrued_usd=current.cost_accrued_usd,
                    uptime_min=current.uptime_min,
                    detail=f"S3 latest_step={snapshot.latest_step}",
                ),
            )
            evidence.event(
                "training.progress",
                latest_step=snapshot.latest_step,
                metric_count=snapshot.metric_count,
                cost_accrued_usd=current.cost_accrued_usd,
                uptime_min=current.uptime_min,
            )
            if snapshot.complete:
                break
            await asyncio.sleep(poll_seconds)

        current = await adapter.status(handle)
        estimated_cost = max(estimated_cost, current.cost_accrued_usd)
        await orchestrator.observe(
            handle,
            ProvisionStatus(
                state=ProvisionState.COLLECTING,
                ssh=current.ssh,
                cost_accrued_usd=current.cost_accrued_usd,
                uptime_min=current.uptime_min,
                detail="P2 S3 completion objects are visible",
            ),
        )
        collection_dir = EVIDENCE_ROOT / job_id / "collected"
        inventory = collector.collect(collection_dir)
        elapsed_seconds = round(time.monotonic() - start_monotonic, 3)
        evidence.event(
            "training.collected",
            elapsed_seconds=elapsed_seconds,
            object_count=len(inventory["objects"]),
            latest_step=inventory["snapshot"]["latest_step"],
        )
        completed = True
        cleanup_trigger = time.monotonic()
    finally:
        if handle is not None:
            try:
                await orchestrator.terminate(
                    handle,
                    reason="P2 full-run completion" if completed else "P2 full-run failure cleanup",
                )
            finally:
                receipt = await _confirm_deleted(adapter, handle)
                cleanup_seconds = round(
                    time.monotonic() - (cleanup_trigger or time.monotonic()), 3
                )
                evidence.event(
                    "training.terminated",
                    external_id=handle.external_id,
                    deletion=asdict(receipt),
                    billing_status="pending",
                    estimated_cost_usd=round(estimated_cost, 6),
                    cleanup_seconds=cleanup_seconds,
                )
                if cleanup_seconds > CLEANUP_SLA_SECONDS:
                    raise LiveExecutionError("full-run cleanup exceeded the 30-minute SLA")
                await _audit_deletion(audit, handle, receipt)
                _schedule_billing(billing_schedule, handle, receipt.checked_at)
    if not completed:
        raise LiveExecutionError("P2 full run did not complete")
    final_job = _job_status(
        job,
        "done",
        {
            "external_id": handle.external_id,
            "billing_status": "pending",
            "estimated_cost_usd": round(estimated_cost, 6),
            "latest_step": P2_TOTAL_STEPS,
            "collection": str(EVIDENCE_ROOT / job_id / "collected"),
        },
    )
    await repositories.jobs.put(final_job)
    return {
        "job_id": job_id,
        "external_id": handle.external_id,
        "billing_status": "pending",
        "estimated_cost_usd": round(estimated_cost, 6),
        "latest_step": P2_TOTAL_STEPS,
        "revision": revision,
        "collection_dir": str(EVIDENCE_ROOT / job_id / "collected"),
        **(
            {"retry_of": replacement_for_external_id}
            if replacement_for_external_id is not None
            else {}
        ),
    }


async def _wait_for_ssh(
    adapter: RunPodAdapter,
    orchestrator: LifecycleOrchestrator,
    handle: ProvisionHandle,
    *,
    timeout_s: int,
) -> ProvisionStatus:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = await orchestrator.tick(handle)
        if status.state == ProvisionState.BOOTSTRAPPING and status.ssh:
            return status
        if status.state in {ProvisionState.FAILED, ProvisionState.TERMINATED}:
            raise LiveExecutionError(f"RunPod did not reach SSH readiness: {status.detail}")
        await asyncio.sleep(15)
    raise LiveExecutionError("RunPod SSH readiness timed out")


async def reconcile_billing(
    path: Path,
    slot: str,
    *,
    now: datetime | None = None,
    adapter: Any | None = None,
    audit: Any | None = None,
) -> dict[str, Any]:
    owns_adapter = adapter is None
    runtime = None
    if adapter is None:
        api_key = os.environ.get("RUNPOD_API_KEY", "")
        if not api_key:
            raise LiveExecutionError("missing required environment variable: RUNPOD_API_KEY")
        adapter = RunPodAdapter(api_key)
    if audit is None:
        runtime = create_database_runtime(DatabaseSettings.from_env())
        audit = DatabaseAuditLog(create_repositories(runtime).provision_audit)
    try:
        return await reconcile_billing_schedule(
            path,
            slot,
            adapter=adapter,
            audit=audit,
            now=now,
        )
    finally:
        if owns_adapter:
            await adapter.aclose()
        if runtime is not None:
            await runtime.close()


def _spec(
    *,
    job_id: str,
    approval_id: str,
    public_key: str,
    budget: float,
    max_runtime: int,
) -> ProvisionSpec:
    return ProvisionSpec(
        job_id=job_id,
        approval_id=approval_id,
        requested_by="p2-executor",
        provider=ProvisionProvider.RUNPOD,
        gpu_class=GPUClass.SMALL_ADA,
        image=RUNPOD_IMAGE,
        container_disk_gb=80,
        env={"VF_STORAGE_BACKEND": "s3", "VF_PROVISION_STAGE": "p2"},
        ports=[22],
        ssh_pubkey=public_key,
        budget_usd_cap=budget,
        max_runtime_min=max_runtime,
    )


def _prepare_and_bootstrap(ssh_endpoint: str, *, evidence: EvidenceLedger) -> str:
    revision = _pushed_clean_revision()
    host, port = _split_ssh(ssh_endpoint)
    key = Path("~/.ssh/id_ed25519").expanduser()
    directory = evidence.path.parent
    directory.mkdir(parents=True, exist_ok=True)
    bundle = directory / f"verifierforge-{revision[:12]}.bundle"
    _run_checked(["git", "bundle", "create", str(bundle), "HEAD"], cwd=ROOT)
    ssh_args = _ssh_args(host, port, key, directory / "known_hosts")
    _run_checked(
        [
            "rsync",
            "-a",
            "--partial",
            "-e",
            shlex.join(ssh_args),
            str(bundle),
            f"{host}:/tmp/verifierforge.bundle",
        ],
        cwd=ROOT,
    )
    remote = f"""set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git tmux rsync python3-venv
rm -rf /workspace/verifierforge
git clone /tmp/verifierforge.bundle /workspace/verifierforge
cd /workspace/verifierforge
git checkout --detach {shlex.quote(revision)}
test "$(git rev-parse HEAD)" = {shlex.quote(revision)}
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-trainer.txt
export HF_HOME=/workspace/hf-cache
mkdir -p "$HF_HOME"
.venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download("{MODEL_ID}")
PY
.venv/bin/python - <<'PY'
import ray, torch, transformers, verl, vllm
print("runtime_ready", torch.__version__, vllm.__version__, transformers.__version__)
PY
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
"""
    log = directory / "bootstrap.log"
    _run_logged([*ssh_args, host, "bash", "-s"], input_text=remote, log_path=log)
    evidence.event("bootstrap.completed", revision=revision, log=str(log))
    return revision


def _launch_s3_job(ssh_endpoint: str, *, job_id: str, config: str) -> None:
    host, port = _split_ssh(ssh_endpoint)
    key = Path("~/.ssh/id_ed25519").expanduser()
    ssh_args = _ssh_args(host, port, key, EVIDENCE_ROOT / "known_hosts")
    payload = json.dumps(local_payload(os.environ), separators=(",", ":"))
    command = (
        "cd /workspace/verifierforge && "
        f".venv/bin/python -m scripts.s3_job_env --launch --root /workspace/verifierforge "
        f"--python /workspace/verifierforge/.venv/bin/python --job {shlex.quote(job_id)} "
        f"--config {shlex.quote(config)}"
    )
    completed = subprocess.run(
        [*ssh_args, host, command],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise LiveExecutionError(
            f"remote S3 job launch failed ({completed.returncode}): {completed.stderr[-2000:]}"
        )
    parsed = json.loads(completed.stdout)
    if parsed.get("status") != "started" or parsed.get("storage") != "s3":
        raise LiveExecutionError("remote S3 job launcher returned an invalid acknowledgement")


def _pushed_clean_revision() -> str:
    if subprocess.run(["git", "diff", "--quiet"], cwd=ROOT).returncode:
        raise LiveExecutionError("tracked worktree changes must be committed before live provisioning")
    head = _output(["git", "rev-parse", "HEAD"], cwd=ROOT)
    origin = _output(["git", "rev-parse", "origin/main"], cwd=ROOT)
    if head != origin:
        raise LiveExecutionError("HEAD must equal origin/main before live provisioning")
    return head


def _public_key() -> str:
    public = Path("~/.ssh/id_ed25519.pub").expanduser()
    private = Path("~/.ssh/id_ed25519").expanduser()
    if public.is_file():
        value = public.read_text(encoding="utf-8").strip()
    elif private.is_file():
        value = _output(["ssh-keygen", "-y", "-f", str(private)])
    else:
        raise LiveExecutionError("~/.ssh/id_ed25519 is required for the disposable pod")
    if not value.startswith("ssh-"):
        raise LiveExecutionError("local SSH public key is invalid")
    return value


def _split_ssh(value: str) -> tuple[str, int]:
    try:
        host, raw_port = value.rsplit(":", 1)
        port = int(raw_port)
    except (ValueError, TypeError):
        raise LiveExecutionError("RunPod SSH endpoint has an invalid shape") from None
    if not host.startswith("root@") or not 1 <= port <= 65535:
        raise LiveExecutionError("RunPod SSH endpoint has an invalid shape")
    return host, port


def _ssh_args(host: str, port: int, key: Path, known_hosts: Path) -> list[str]:
    del host
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    return [
        "ssh",
        "-i",
        str(key),
        "-p",
        str(port),
        "-o",
        "ConnectTimeout=20",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
    ]


def _run_checked(command: list[str], *, cwd: Path) -> None:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise LiveExecutionError(
            f"command failed ({completed.returncode}): {completed.stderr[-2000:]}"
        )


def _run_logged(
    command: list[str], *, input_text: str, log_path: Path
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            input=input_text,
            text=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if completed.returncode:
        tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:])
        raise LiveExecutionError(f"remote bootstrap failed ({completed.returncode}):\n{tail}")


def _output(command: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise LiveExecutionError(f"command failed ({completed.returncode})")
    return completed.stdout.strip()


def _job_status(job: JobRecord, status: str, summary: Mapping[str, Any]) -> JobRecord:
    return JobRecord(
        job_id=job.job_id,
        template=job.template,
        status=status,
        config_json=job.config_json,
        created_at=job.created_at,
        s3_prefix=job.s3_prefix,
        summary_json={**job.summary_json, **summary},
    )


def _require_local_environment() -> None:
    required = (
        "RUNPOD_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_DEFAULT_REGION",
        "VF_S3_BUCKET",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise LiveExecutionError(f"missing required environment variables: {', '.join(missing)}")


def _check_wave_budget(value: float) -> None:
    if value >= P2_WAVE_BUDGET_USD:
        raise LiveExecutionError(
            f"P2 wave budget reached: ${value:.4f} >= ${P2_WAVE_BUDGET_USD:.2f}"
        )


def _s3_client():
    import boto3

    return boto3.client("s3", region_name=os.environ.get("VF_S3_REGION") or os.environ.get("AWS_DEFAULT_REGION"))


def _assert_clean_s3_prefix(client: Any, *, bucket: str, prefix: str, job_id: str) -> None:
    job_prefix = "/".join(
        part for part in (prefix.strip("/"), "jobs", job_id) if part
    )
    response = client.list_objects_v2(
        Bucket=bucket,
        Prefix=f"{job_prefix}/",
        MaxKeys=1,
    )
    if response.get("Contents"):
        raise LiveExecutionError(f"retry S3 prefix is not empty: {job_prefix}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--execute-live", action="store_true")
    action.add_argument("--reconcile-billing", type=Path)
    parser.add_argument("--approval-id")
    parser.add_argument("--resume-gold-evidence", type=Path)
    parser.add_argument("--resume-full-evidence", type=Path)
    parser.add_argument("--billing-slot", choices=tuple(BILLING_SLOTS))
    parser.add_argument("--poll-seconds", type=int, default=TRAINING_POLL_SECONDS)
    args = parser.parse_args()
    if args.execute_live and not args.approval_id:
        parser.error("--approval-id is required with --execute-live")
    if args.execute_live and args.billing_slot:
        parser.error("--billing-slot is only valid with --reconcile-billing")
    if args.reconcile_billing and not args.billing_slot:
        parser.error("--billing-slot is required with --reconcile-billing")
    if args.resume_gold_evidence and args.resume_full_evidence:
        parser.error("--resume-gold-evidence and --resume-full-evidence are mutually exclusive")
    if args.reconcile_billing and (
        args.approval_id or args.resume_gold_evidence or args.resume_full_evidence
    ):
        parser.error("approval/resume arguments are invalid with --reconcile-billing")
    if args.execute_live and args.poll_seconds < 30:
        parser.error("--poll-seconds must be at least 30")
    return args


def main() -> None:
    load_dotenv(dotenv_path=ROOT / ".env")
    args = parse_args()
    if args.reconcile_billing:
        result = asyncio.run(
            reconcile_billing(args.reconcile_billing, args.billing_slot)
        )
    else:
        result = asyncio.run(
            execute_live(
                args.approval_id,
                poll_seconds=args.poll_seconds,
                resume_gold_evidence=args.resume_gold_evidence,
                resume_full_evidence=args.resume_full_evidence,
            )
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
