"""Zero-cost mock lifecycle demonstration for Provisioner P-1."""

from __future__ import annotations

import argparse
import asyncio
import json

from app.provisioning import (
    InMemoryAuditLog,
    KillSwitch,
    LifecycleOrchestrator,
    MockAdapter,
    MockFailureMode,
    ProvisioningPolicy,
)
from core.provisioning_contracts import (
    GPUClass,
    ProvisionProvider,
    ProvisionSpec,
    ProvisionState,
)


def _spec(*, budget: float = 5.0, max_runtime_min: int = 30) -> ProvisionSpec:
    return ProvisionSpec(
        job_id="job-demo",
        approval_id="approval-demo",
        requested_by="demo",
        provider=ProvisionProvider.RUNPOD,
        gpu_class=GPUClass.SMALL_ADA,
        image="ghcr.io/verifierforge/trainer:dry-run",
        container_disk_gb=40,
        region_pref=["mock-region-1"],
        env={"VF_STORAGE_BACKEND": "s3", "VF_MODE": "dry_run"},
        ports=[22, 8000],
        ssh_pubkey="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeDryRunKey demo",
        budget_usd_cap=budget,
        max_runtime_min=max_runtime_min,
    )


async def _run(args: argparse.Namespace) -> dict[str, object]:
    failure_mode = {
        "create-timeout": MockFailureMode.CREATE_TIMEOUT,
        "ssh-unreachable": MockFailureMode.SSH_UNREACHABLE,
        "mid-run-termination": MockFailureMode.MID_RUN_TERMINATION,
    }.get(args.scenario)
    budget = 0.05 if args.scenario == "budget-cap" else 5.0
    runtime = 2 if args.scenario == "runtime-cap" else 30
    audit = InMemoryAuditLog()
    kill_switch = KillSwitch()
    orchestrator = LifecycleOrchestrator(
        adapter=MockAdapter(failure_mode=failure_mode),
        audit_log=audit,
        policy=ProvisioningPolicy(
            autoprovision_enabled=True,
            max_concurrent_active=1,
            max_ticks=20,
        ),
        kill_switch=kill_switch,
    )
    spec = _spec(budget=budget, max_runtime_min=runtime)

    if args.scenario == "kill-switch":
        handle = await orchestrator.request(spec)
        kill_switch.activate("demo kill switch")
        status = await orchestrator.tick(handle)
    else:
        status = await orchestrator.run_to_completion(spec)

    active_handles = await orchestrator.adapter.list_active()
    return {
        "scenario": args.scenario,
        "state": status.state.value,
        "detail": status.detail,
        "audit_events": len(audit.events),
        "active_handles": [handle.external_id for handle in active_handles],
        "feature_flag_default": ProvisioningPolicy.from_env({}).autoprovision_enabled,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a zero-cost mock provision lifecycle.")
    parser.add_argument("--mock", action="store_true", help="required dry-run adapter selector")
    parser.add_argument(
        "--scenario",
        choices=[
            "happy-path",
            "budget-cap",
            "runtime-cap",
            "kill-switch",
            "create-timeout",
            "ssh-unreachable",
            "mid-run-termination",
        ],
        default="happy-path",
    )
    args = parser.parse_args()
    if not args.mock:
        raise SystemExit("--mock is required; P-1 has no real provider adapter")
    print(json.dumps(asyncio.run(_run(args)), sort_keys=True))


if __name__ == "__main__":
    main()
