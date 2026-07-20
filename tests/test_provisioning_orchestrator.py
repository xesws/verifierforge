from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.provisioning import (
    DatabaseAuditLog,
    DatabaseActiveProvisionRegistry,
    InMemoryAuditLog,
    KillSwitch,
    LifecycleOrchestrator,
    MockAdapter,
    MockFailureMode,
    ProvisionAuditError,
    ProvisionNoCapacity,
    ProvisioningPolicy,
    ProvisionRejected,
)
from app.db.engine import create_database_runtime
from app.db.migration import migrate_sqlite
from app.db.records import (
    AgentDecisionRecord,
    ApprovalRecord as DatabaseApprovalRecord,
    ProvisionEventRecord,
)
from app.db.repositories import create_repositories
from app.db.settings import DatabaseSettings
from datetime import datetime, timezone
from core.provisioning_contracts import (
    GPUClass,
    ProvisionHandle,
    ProvisionProvider,
    ProvisionSpec,
    ProvisionState,
    ProvisionStatus,
)


def _run(coro):
    return asyncio.run(coro)


def _spec(job_id: str = "job-1", *, budget: float = 5.0, runtime: int = 30) -> ProvisionSpec:
    return ProvisionSpec(
        job_id=job_id,
        approval_id=f"approval-{job_id}",
        requested_by="owner-a",
        provider=ProvisionProvider.RUNPOD,
        gpu_class=GPUClass.SMALL_ADA,
        image="ghcr.io/verifierforge/trainer:dry-run",
        container_disk_gb=40,
        region_pref=["mock-region-1"],
        env={"VF_STORAGE_BACKEND": "s3", "VF_MODE": "dry_run"},
        ports=[22, 8000],
        ssh_pubkey="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeDryRunKey owner-a",
        budget_usd_cap=budget,
        max_runtime_min=runtime,
    )


def _orchestrator(
    adapter: MockAdapter | None = None,
    *,
    audit: InMemoryAuditLog | None = None,
    kill_switch: KillSwitch | None = None,
    max_concurrent: int = 1,
) -> tuple[LifecycleOrchestrator, MockAdapter, InMemoryAuditLog]:
    adapter = adapter if adapter is not None else MockAdapter()
    audit = audit if audit is not None else InMemoryAuditLog()
    orchestrator = LifecycleOrchestrator(
        adapter=adapter,
        audit_log=audit,
        policy=ProvisioningPolicy(
            autoprovision_enabled=True,
            max_concurrent_active=max_concurrent,
            max_ticks=20,
        ),
        kill_switch=kill_switch,
    )
    return orchestrator, adapter, audit


def test_mock_lifecycle_full_state_path_and_terminal_tick_idempotence() -> None:
    orchestrator, adapter, audit = _orchestrator()
    handle = _run(orchestrator.request(_spec()))
    states: list[ProvisionState] = []
    for _ in range(8):
        status = _run(orchestrator.tick(handle))
        states.append(status.state)
        if status.state in {ProvisionState.TERMINATED, ProvisionState.FAILED}:
            break

    assert states == [
        ProvisionState.PROVISIONING,
        ProvisionState.BOOTSTRAPPING,
        ProvisionState.RUNNING,
        ProvisionState.COLLECTING,
        ProvisionState.TERMINATED,
    ]
    audit_count = len(audit.events)
    assert _run(orchestrator.tick(handle)).state == ProvisionState.TERMINATED
    assert len(audit.events) == audit_count
    assert _run(adapter.list_active()) == []


def test_run_to_completion_uses_mock_adapter_and_leaves_no_active_handle() -> None:
    orchestrator, adapter, _audit = _orchestrator()

    status = _run(orchestrator.run_to_completion(_spec()))

    assert status.state == ProvisionState.TERMINATED
    assert _run(adapter.list_active()) == []


class _NoCapacityAdapter(MockAdapter):
    async def provision(self, spec: ProvisionSpec) -> ProvisionHandle:
        raise ProvisionNoCapacity("no_capacity: all approved candidates are unavailable")


def test_no_capacity_converges_to_failed_with_explicit_audit() -> None:
    orchestrator, adapter, audit = _orchestrator(_NoCapacityAdapter())

    status = _run(orchestrator.run_to_completion(_spec()))

    assert status.state == ProvisionState.FAILED
    assert status.detail == "no_capacity: all approved candidates are unavailable"
    failed = next(event for event in audit.events if event.action == "provision.failed")
    assert failed.after_state == ProvisionState.FAILED
    assert failed.reason == "no_capacity"
    assert _run(adapter.list_active()) == []


class _SelectedGPUAdapter(MockAdapter):
    async def provision(self, spec: ProvisionSpec) -> ProvisionHandle:
        handle = await super().provision(spec)
        return handle.model_copy(
            update={
                "labels": {
                    **handle.labels,
                    "gpu_model": "NVIDIA RTX 4000 Ada Generation",
                    "gpu_display_name": "RTX 4000 Ada",
                    "cloud_type": "COMMUNITY",
                    "hourly_price_usd": "0.200000",
                }
            }
        )


def test_created_audit_carries_selected_gpu_and_numeric_price() -> None:
    orchestrator, _adapter, audit = _orchestrator(_SelectedGPUAdapter())

    _run(orchestrator.request(_spec()))

    created = next(event for event in audit.events if event.action == "provision.created")
    assert created.detail == {
        "gpu_model": "NVIDIA RTX 4000 Ada Generation",
        "gpu_display_name": "RTX 4000 Ada",
        "cloud_type": "COMMUNITY",
        "hourly_price_usd": 0.2,
    }


def test_workload_observations_advance_running_and_collecting_without_provider_leakage() -> None:
    orchestrator, adapter, _audit = _orchestrator()
    handle = _run(orchestrator.request(_spec()))
    assert _run(orchestrator.tick(handle)).state == ProvisionState.PROVISIONING
    ready = _run(orchestrator.tick(handle))
    assert ready.state == ProvisionState.BOOTSTRAPPING
    running = _run(
        orchestrator.observe(
            handle,
            ProvisionStatus(
                state=ProvisionState.RUNNING,
                ssh=ready.ssh,
                cost_accrued_usd=0.1,
                uptime_min=2,
                detail="workload started",
            ),
        )
    )
    assert running.state == ProvisionState.RUNNING
    collecting = _run(
        orchestrator.observe(
            handle,
            ProvisionStatus(
                state=ProvisionState.COLLECTING,
                ssh=ready.ssh,
                cost_accrued_usd=0.2,
                uptime_min=3,
                detail="S3 completion visible",
            ),
        )
    )
    assert collecting.state == ProvisionState.COLLECTING
    assert _run(orchestrator.terminate(handle)).state == ProvisionState.TERMINATED
    assert _run(adapter.list_active()) == []


def test_max_concurrency_fuse_rejects_second_request_before_provider_mutation() -> None:
    orchestrator, adapter, audit = _orchestrator(max_concurrent=1)
    _run(orchestrator.request(_spec("job-1")))

    with pytest.raises(ProvisionRejected):
        _run(orchestrator.request(_spec("job-2")))

    assert [handle.external_id for handle in _run(adapter.list_active())] == ["mock-0001"]
    assert "provision.rejected" in {event.action for event in audit.events}
    _run(orchestrator.terminate_all(reason="test cleanup"))
    assert _run(adapter.list_active()) == []


def test_budget_fuse_terminates_and_marks_failed() -> None:
    orchestrator, adapter, audit = _orchestrator(MockAdapter(cost_per_poll_usd=1.0))
    handle = _run(orchestrator.request(_spec(budget=0.5)))

    status = _run(orchestrator.tick(handle))

    assert status.state == ProvisionState.FAILED
    assert status.detail == "single job budget cap reached"
    assert _run(adapter.list_active()) == []
    assert "budget.terminated" in {event.action for event in audit.events}


def test_runtime_fuse_terminates_and_marks_failed() -> None:
    orchestrator, adapter, audit = _orchestrator(MockAdapter())
    handle = _run(orchestrator.request(_spec(runtime=2)))

    assert _run(orchestrator.tick(handle)).state == ProvisionState.PROVISIONING
    status = _run(orchestrator.tick(handle))

    assert status.state == ProvisionState.FAILED
    assert status.detail == "maximum runtime reached"
    assert _run(adapter.list_active()) == []
    assert "runtime.terminated" in {event.action for event in audit.events}


def test_kill_switch_terminates_active_handle() -> None:
    kill_switch = KillSwitch()
    orchestrator, adapter, audit = _orchestrator(kill_switch=kill_switch)
    handle = _run(orchestrator.request(_spec()))

    kill_switch.activate("operator kill")
    status = _run(orchestrator.tick(handle))

    assert status.state == ProvisionState.TERMINATED
    assert status.detail == "operator kill"
    assert _run(adapter.list_active()) == []
    assert "kill_switch.terminated" in {event.action for event in audit.events}


class _Registry:
    def __init__(self, active_ids: set[str]) -> None:
        self.active_ids = active_ids

    async def is_active(self, handle: ProvisionHandle) -> bool:
        return handle.external_id in self.active_ids


def test_orphan_reaper_terminates_provider_handle_missing_from_registry() -> None:
    orchestrator, adapter, audit = _orchestrator()
    handle = _run(orchestrator.request(_spec()))

    reaped = _run(orchestrator.reap_orphans(_Registry(set())))

    assert reaped == [handle]
    assert _run(adapter.list_active()) == []
    assert "orphan.reaped" in {event.action for event in audit.events}


def test_audit_fuse_terminates_created_handle_when_durable_append_fails() -> None:
    audit = InMemoryAuditLog(fail_on_actions={"provision.created"})
    orchestrator, adapter, _audit = _orchestrator(audit=audit)

    with pytest.raises(ProvisionAuditError):
        _run(orchestrator.request(_spec()))

    assert _run(adapter.list_active()) == []


@pytest.mark.parametrize(
    ("failure_mode", "expected_detail"),
    [
        (MockFailureMode.CREATE_TIMEOUT, "mock create timed out before handle allocation"),
        (MockFailureMode.SSH_UNREACHABLE, "ssh unreachable during bootstrapping"),
        (MockFailureMode.MID_RUN_TERMINATION, "mock provider reported mid-run termination"),
    ],
)
def test_failure_injections_converge_without_active_handles(
    failure_mode: MockFailureMode,
    expected_detail: str,
) -> None:
    orchestrator, adapter, _audit = _orchestrator(MockAdapter(failure_mode=failure_mode))

    status = _run(orchestrator.run_to_completion(_spec()))

    assert status.state == ProvisionState.FAILED
    assert expected_detail in status.detail
    assert _run(adapter.list_active()) == []


class _IllegalTransitionAdapter(MockAdapter):
    async def status(self, handle: ProvisionHandle) -> ProvisionStatus:
        return ProvisionStatus(
            state=ProvisionState.COLLECTING,
            ssh=handle.ssh,
            cost_accrued_usd=0.1,
            uptime_min=1,
            detail="illegal jump",
        )


def test_illegal_transition_terminates_and_fails() -> None:
    orchestrator, adapter, audit = _orchestrator(_IllegalTransitionAdapter())
    handle = _run(orchestrator.request(_spec()))

    status = _run(orchestrator.tick(handle))

    assert status.state == ProvisionState.FAILED
    assert "illegal lifecycle transition" in status.detail
    assert _run(adapter.list_active()) == []
    assert "lifecycle.failed" in {event.action for event in audit.events}


def test_package_has_no_real_provider_or_training_side_effect_imports() -> None:
    source = "\n".join(
        path.read_text()
        for root in [Path("app/provisioning"), Path("scripts")]
        for path in root.glob("provisioning*.py")
    )
    forbidden = [
        "import runpod",
        "import nebius",
        "import requests",
        "import subprocess",
        "from trainer",
        "import trainer",
        "scripts.vf",
    ]
    assert not any(token in source for token in forbidden)


def test_database_audit_log_persists_full_mock_lifecycle(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = DatabaseSettings.sqlite(tmp_path / "provisioning.sqlite3")
        await migrate_sqlite(settings)
        runtime = create_database_runtime(settings)
        repositories = create_repositories(runtime)
        now = datetime.now(timezone.utc)
        await repositories.agent_decisions.put(
            AgentDecisionRecord(
                id="decision-job-1",
                cluster_id="data-pull-sql",
                decision="forge",
                rationale="mock approved forge",
                confidence=1.0,
                config_json=None,
                trace_s3_key="vf/trace.json",
                model_name="mock",
                created_at=now,
            )
        )
        await repositories.approvals.put(
            DatabaseApprovalRecord(
                id="approval-job-1",
                decision_id="decision-job-1",
                approved_by="owner-a",
                approved_at=now,
            )
        )
        orchestrator = LifecycleOrchestrator(
            adapter=MockAdapter(),
            audit_log=DatabaseAuditLog(repositories.provision_audit),
            policy=ProvisioningPolicy(
                autoprovision_enabled=True,
                max_concurrent_active=1,
                max_ticks=20,
            ),
        )
        assert (await orchestrator.run_to_completion(_spec())).state == ProvisionState.TERMINATED
        events = await repositories.provision_audit.list_for_approval(
            "approval-job-1"
        )
        assert [event.action for event in events][0] == "provision.requested"
        assert events[-1].status == "TERMINATED"
        await runtime.close()

    _run(scenario())


def test_database_registry_requires_bound_handle_without_terminal_event(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = DatabaseSettings.sqlite(tmp_path / "registry.sqlite3")
        await migrate_sqlite(settings)
        runtime = create_database_runtime(settings)
        repositories = create_repositories(runtime)
        now = datetime.now(timezone.utc)
        await repositories.agent_decisions.put(
            AgentDecisionRecord(
                id="decision-registry",
                cluster_id="data-pull-sql",
                decision="forge",
                rationale="approved",
                confidence=1.0,
                config_json=None,
                trace_s3_key=None,
                model_name="mock",
                created_at=now,
            )
        )
        await repositories.approvals.put(
            DatabaseApprovalRecord(
                id="approval-registry",
                decision_id="decision-registry",
                approved_by="owner",
                approved_at=now,
            )
        )
        handle = ProvisionHandle(
            provider="runpod",
            external_id="pod-registry",
            job_id="job-registry",
            approval_id="approval-registry",
        )
        registry = DatabaseActiveProvisionRegistry(
            approvals=repositories.approvals,
            provision_audit=repositories.provision_audit,
        )
        assert await registry.is_active(handle) is False
        await repositories.approvals.bind_provision_handle(
            "approval-registry", "pod-registry"
        )
        assert await registry.is_active(handle) is True
        await repositories.provision_audit.append(
            ProvisionEventRecord(
                id="terminal-event",
                approval_id="approval-registry",
                job_id="job-registry",
                provider="runpod",
                action="provision.terminated",
                status="TERMINATED",
                actor="system",
                occurred_at=now,
                detail_json={"external_id": "pod-registry"},
            )
        )
        assert await registry.is_active(handle) is False
        await runtime.close()

    _run(scenario())
