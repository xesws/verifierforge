"""Execute the approved P2 RunPod lifecycle from local credentials only."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
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
EVIDENCE_ROOT = ROOT / "runs" / "provisioning" / "v0.28.0"
MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"
TRAINING_POLL_SECONDS = 300
SSH_READY_TIMEOUT_SECONDS = 15 * 60
BILLING_TIMEOUT_SECONDS = 15 * 60
CLEANUP_SLA_SECONDS = 30 * 60


class LiveExecutionError(RuntimeError):
    pass


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


async def execute_live(approval_id: str, *, poll_seconds: int) -> dict[str, Any]:
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
    total_billed = 0.0
    try:
        approval = await repositories.approvals.get(approval_id)
        if approval is None:
            raise LiveExecutionError("approval does not exist")
        if approval.provision_handle is not None:
            raise LiveExecutionError("approval is already bound to a provision handle")
        decision = await repositories.agent_decisions.get(approval.decision_id)
        if decision is None or decision.decision != "forge" or decision.config_json is None:
            raise LiveExecutionError("approval does not reference a persisted forge decision")
        config = validate_p2_config(decision.config_json)
        job_id = f"p2-{approval.id[:20]}"
        evidence = EvidenceLedger(
            EVIDENCE_ROOT / job_id / "lifecycle.json",
            approval_id=approval.id,
            job_id=job_id,
        )
        inventory = await adapter.list_account_pods()
        evidence.event(
            "account.inventory",
            pod_count=len(inventory),
            managed_active_count=len(await adapter.list_active()),
            # Names and IDs are operational metadata; env and provider payloads are omitted.
            pods=[
                {
                    "id": str(pod.get("id", "")),
                    "name": str(pod.get("name", "")),
                    "status": str(pod.get("desiredStatus") or pod.get("status") or ""),
                }
                for pod in inventory
            ],
        )
        if await adapter.list_active():
            raise LiveExecutionError("a VerifierForge-managed RunPod resource is already active")

        public_key = _public_key()
        total_billed += await _gold_path(
            adapter=adapter,
            orchestrator=orchestrator,
            audit=audit,
            approval_id=approval.id,
            public_key=public_key,
            evidence=evidence,
        )
        _check_wave_budget(total_billed)
        total_billed += await _orphan_probe(
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
        )
        _check_wave_budget(total_billed)

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
            prior_billed=total_billed,
            poll_seconds=poll_seconds,
        )
        total_billed += float(result["billing_usd"])
        _check_wave_budget(total_billed)
        evidence.finish(status="done", wave_billing_usd=round(total_billed, 6), result=result)
        return {**result, "wave_billing_usd": round(total_billed, 6)}
    except Exception as error:
        if "evidence" in locals():
            evidence.finish(
                status="failed",
                error_type=type(error).__name__,
                error=str(error)[:2000],
                wave_billing_usd=round(total_billed, 6),
            )
        raise
    finally:
        await adapter.aclose()
        await runtime.close()


async def _gold_path(
    *,
    adapter: RunPodAdapter,
    orchestrator: LifecycleOrchestrator,
    audit: DatabaseAuditLog,
    approval_id: str,
    public_key: str,
    evidence: EvidenceLedger,
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
    billing_amount: float | None = None
    try:
        handle = await orchestrator.request(spec)
        evidence.event("gold.created", external_id=handle.external_id)
        status = await _wait_for_ssh(adapter, orchestrator, handle, timeout_s=SSH_READY_TIMEOUT_SECONDS)
        evidence.event(
            "gold.ready",
            external_id=handle.external_id,
            ssh=status.ssh,
            cost_accrued_usd=status.cost_accrued_usd,
        )
    finally:
        if handle is not None:
            await orchestrator.terminate(handle, reason="P2 gold-path teardown")
            billing = await _confirm_deleted_and_billed(
                adapter, handle, start_time=handle.created_at
            )
            cleanup_seconds = round(time.monotonic() - trigger, 3)
            if cleanup_seconds > CLEANUP_SLA_SECONDS:
                raise LiveExecutionError("gold-path cleanup exceeded the 30-minute SLA")
            evidence.event(
                "gold.terminated",
                external_id=handle.external_id,
                cleanup_seconds=cleanup_seconds,
                billing_usd=billing.amount_usd,
                time_billed_ms=billing.time_billed_ms,
            )
            await _audit_billing(audit, handle, billing.amount_usd, billing.time_billed_ms)
            billing_amount = billing.amount_usd
    if billing_amount is None:
        raise LiveExecutionError("gold path failed before a RunPod handle was allocated")
    return billing_amount


async def _orphan_probe(
    *,
    adapter: RunPodAdapter,
    orchestrator: LifecycleOrchestrator,
    audit: DatabaseAuditLog,
    registry: DatabaseActiveProvisionRegistry,
    approval_id: str,
    public_key: str,
    evidence: EvidenceLedger,
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
        reaped = await orchestrator.reap_orphans(
            registry, actor="p2-orphan-probe", reason="intentional P2 orphan-reaper proof"
        )
        if [item.external_id for item in reaped] != [handle.external_id]:
            raise LiveExecutionError("orphan reaper did not terminate exactly its test resource")
        billing = await _confirm_deleted_and_billed(adapter, handle, start_time=handle.created_at)
        cleanup_seconds = round(time.monotonic() - trigger, 3)
        if cleanup_seconds > CLEANUP_SLA_SECONDS:
            raise LiveExecutionError("orphan cleanup exceeded the 30-minute SLA")
        evidence.event(
            "orphan.reaped",
            external_id=handle.external_id,
            cleanup_seconds=cleanup_seconds,
            billing_usd=billing.amount_usd,
            time_billed_ms=billing.time_billed_ms,
        )
        await _audit_billing(audit, handle, billing.amount_usd, billing.time_billed_ms)
        return billing.amount_usd
    except Exception:
        if handle is not None and await adapter.get_pod(handle.external_id) is not None:
            await adapter.terminate(handle)
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
    prior_billed: float,
    poll_seconds: int,
) -> dict[str, Any]:
    s3_prefix = os.environ.get("VF_S3_PREFIX", "vf").strip("/")
    job = JobRecord(
        job_id=job_id,
        template="nl2sql",
        status="queued",
        config_json=config.model_dump(mode="json"),
        created_at=datetime.now(timezone.utc),
        s3_prefix=f"{s3_prefix}/jobs/{job_id}",
        summary_json={"approval_id": approval.id, "profile": P2_CONFIG_NAME},
    )
    await repositories.jobs.put(job)
    spec = _spec(
        job_id=job_id,
        approval_id=approval.id,
        public_key=public_key,
        budget=min(float(config.budget_usd_cap), P2_WAVE_BUDGET_USD - prior_billed),
        max_runtime=P2_MAX_RUNTIME_MIN,
    )
    handle: ProvisionHandle | None = None
    cleanup_trigger: float | None = None
    start_monotonic = time.monotonic()
    completed = False
    try:
        handle = await orchestrator.request(spec)
        try:
            await repositories.approvals.bind_provision_handle(approval.id, handle.external_id)
        except Exception:
            cleanup_trigger = time.monotonic()
            raise
        evidence.event("training.created", external_id=handle.external_id)
        await repositories.jobs.put(_job_status(job, "running", {"external_id": handle.external_id}))
        ready = await _wait_for_ssh(adapter, orchestrator, handle, timeout_s=SSH_READY_TIMEOUT_SECONDS)
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
            current = await adapter.status(handle)
            if current.state in {ProvisionState.FAILED, ProvisionState.TERMINATED}:
                cleanup_trigger = time.monotonic()
                raise LiveExecutionError(f"RunPod terminated during training: {current.detail}")
            if prior_billed + current.cost_accrued_usd >= P2_WAVE_BUDGET_USD:
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
        billing_usd = 0.0
        billed_ms = 0
        if handle is not None:
            try:
                await orchestrator.terminate(
                    handle,
                    reason="P2 full-run completion" if completed else "P2 full-run failure cleanup",
                )
            finally:
                billing = await _confirm_deleted_and_billed(
                    adapter, handle, start_time=handle.created_at
                )
                billing_usd = billing.amount_usd
                billed_ms = billing.time_billed_ms
                cleanup_seconds = round(
                    time.monotonic() - (cleanup_trigger or time.monotonic()), 3
                )
                evidence.event(
                    "training.terminated",
                    external_id=handle.external_id,
                    billing_usd=billing_usd,
                    time_billed_ms=billed_ms,
                    cleanup_seconds=cleanup_seconds,
                )
                if cleanup_seconds > CLEANUP_SLA_SECONDS:
                    raise LiveExecutionError("full-run cleanup exceeded the 30-minute SLA")
                await _audit_billing(audit, handle, billing_usd, billed_ms)
    if not completed:
        raise LiveExecutionError("P2 full run did not complete")
    final_job = _job_status(
        job,
        "done",
        {
            "external_id": handle.external_id,
            "billing_usd": billing_usd,
            "time_billed_ms": billed_ms,
            "latest_step": P2_TOTAL_STEPS,
            "collection": str(EVIDENCE_ROOT / job_id / "collected"),
        },
    )
    await repositories.jobs.put(final_job)
    return {
        "job_id": job_id,
        "external_id": handle.external_id,
        "billing_usd": billing_usd,
        "time_billed_ms": billed_ms,
        "latest_step": P2_TOTAL_STEPS,
        "revision": revision,
        "collection_dir": str(EVIDENCE_ROOT / job_id / "collected"),
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


async def _confirm_deleted_and_billed(
    adapter: RunPodAdapter,
    handle: ProvisionHandle,
    *,
    start_time: datetime,
):
    deadline = time.monotonic() + BILLING_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        pod = await adapter.get_pod(handle.external_id)
        billing = await adapter.billing(handle.external_id, start_time=start_time)
        if pod is None and billing.records:
            return billing
        await asyncio.sleep(120)
    raise LiveExecutionError("RunPod deletion/billing receipt was not confirmed within 15 minutes")


async def _audit_billing(
    audit: DatabaseAuditLog,
    handle: ProvisionHandle,
    amount_usd: float,
    time_billed_ms: int,
) -> None:
    await audit.append(
        ProvisionAuditEvent(
            actor="p2-executor",
            job_id=handle.job_id,
            approval_id=handle.approval_id,
            action="billing.confirmed",
            provider=handle.provider,
            external_id=handle.external_id,
            before_state=ProvisionState.TERMINATED,
            after_state=ProvisionState.TERMINATED,
            reason="provider deletion observed and billing receipt returned",
            detail={"amount_usd": amount_usd, "time_billed_ms": time_billed_ms},
        )
    )


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=TRAINING_POLL_SECONDS)
    args = parser.parse_args()
    if not args.execute_live:
        parser.error("paid provisioning requires the explicit --execute-live flag")
    if args.poll_seconds < 30:
        parser.error("--poll-seconds must be at least 30")
    return args


def main() -> None:
    load_dotenv(dotenv_path=ROOT / ".env")
    args = parse_args()
    result = asyncio.run(execute_live(args.approval_id, poll_seconds=args.poll_seconds))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
