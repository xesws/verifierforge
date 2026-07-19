from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.provisioning.audit import InMemoryAuditLog
from app.provisioning.runpod import RunPodBilling
from app.provisioning.termination import TerminationEvidenceError
from core.provisioning_contracts import ProvisionHandle, ProvisionProvider
from scripts.provision_runpod import (
    EvidenceLedger,
    LiveExecutionError,
    _check_wave_budget,
    _confirm_deleted,
    _resume_gold_path,
    _schedule_billing,
    reconcile_billing,
)


def _run(coro):
    return asyncio.run(coro)


def _handle(external_id: str = "pod-1") -> ProvisionHandle:
    return ProvisionHandle(
        provider=ProvisionProvider.RUNPOD,
        external_id=external_id,
        job_id="p2-job",
        approval_id="approval-1",
        created_at=datetime(2026, 7, 19, 8, tzinfo=timezone.utc),
    )


class _DeletionAdapter:
    def __init__(self, *, pod=None, inventory=None):
        self.pod = pod
        self.inventory = list(inventory or [])
        self.billing_calls = 0

    async def get_pod(self, _external_id):
        return self.pod

    async def list_account_pods(self):
        return list(self.inventory)

    async def billing(self, *_args, **_kwargs):
        self.billing_calls += 1
        raise AssertionError("the synchronous deletion gate must not call billing")


def test_delete_gate_passes_without_billing_when_raw_prefix_is_empty() -> None:
    adapter = _DeletionAdapter(inventory=[{"name": "owner-pod"}])
    receipt = _run(_confirm_deleted(adapter, _handle(), timeout_s=0, poll_s=0))
    assert receipt.target_absent is True
    assert receipt.vf_auto_prefix_count == 0
    assert adapter.billing_calls == 0


def test_delete_gate_rejects_any_raw_prefix_even_when_target_is_absent() -> None:
    adapter = _DeletionAdapter(
        inventory=[{"name": "vf-auto-prefix-only", "desiredStatus": "EXITED"}]
    )
    with pytest.raises(TerminationEvidenceError, match="vf_auto_prefix_count=1"):
        _run(_confirm_deleted(adapter, _handle(), timeout_s=0, poll_s=0))


def test_delete_gate_rejects_target_still_present() -> None:
    pod = {"id": "pod-1", "name": "owner-pod"}
    adapter = _DeletionAdapter(pod=pod, inventory=[pod])
    with pytest.raises(TerminationEvidenceError, match="target_present=True"):
        _run(_confirm_deleted(adapter, _handle(), timeout_s=0, poll_s=0))


class _AuditStore:
    def __init__(self, records):
        self.records = records

    async def list_for_approval(self, _approval_id):
        return list(self.records)


def test_resume_gold_preserves_failed_evidence_and_creates_no_second_gold(
    tmp_path: Path,
) -> None:
    approval_id = "approval-12345678901234567890"
    job_id = f"p2-{approval_id[:20]}"
    external_id = "gold-pod"
    created_at = datetime(2026, 7, 19, 7, 56, 10, tzinfo=timezone.utc)
    deleted_at = created_at + timedelta(seconds=34)
    previous = tmp_path / "old-lifecycle.json"
    previous_payload = {
        "schema_version": 1,
        "approval_id": approval_id,
        "job_id": job_id,
        "status": "failed",
        "error": "RunPod deletion/billing receipt was not confirmed within 15 minutes",
        "events": [
            {
                "timestamp": created_at.isoformat(),
                "action": "gold.created",
                "external_id": external_id,
            },
            {
                "timestamp": (created_at + timedelta(seconds=30)).isoformat(),
                "action": "gold.ready",
                "external_id": external_id,
                "cost_accrued_usd": 0.004,
            },
        ],
    }
    previous.write_text(json.dumps(previous_payload), encoding="utf-8")
    before = previous.read_bytes()
    record = SimpleNamespace(
        action="provision.terminated",
        occurred_at=deleted_at,
        detail_json={"external_id": external_id},
    )
    adapter = _DeletionAdapter(inventory=[])
    audit = InMemoryAuditLog()
    ledger = EvidenceLedger(
        tmp_path / "new" / "lifecycle.json",
        approval_id=approval_id,
        job_id=job_id,
    )
    schedule = tmp_path / "new" / "billing-schedule.json"

    cost = _run(
        _resume_gold_path(
            adapter=adapter,
            audit=audit,
            provision_audit=_AuditStore([record]),
            approval_id=approval_id,
            previous_path=previous,
            evidence=ledger,
            billing_schedule=schedule,
        )
    )

    assert cost == 0.004
    assert previous.read_bytes() == before
    assert [event.action for event in audit.events] == ["provider.deletion_confirmed"]
    assert ledger.payload["events"][0]["action"] == "gold.cleanup-admitted"
    saved = json.loads(schedule.read_text(encoding="utf-8"))
    assert [item["external_id"] for item in saved["resources"]] == [external_id]
    assert saved["resources"][0]["slots"]["plus-1h"]["due_at"] == (
        deleted_at + timedelta(hours=1)
    ).isoformat()


class _BillingAdapter:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def billing(self, _external_id, *, start_time):
        assert start_time.tzinfo is not None
        self.calls += 1
        return self.responses.pop(0)


def test_pending_billing_slots_are_idempotent_and_audited_once(tmp_path: Path) -> None:
    schedule = tmp_path / "billing-schedule.json"
    handle = _handle()
    deleted_at = datetime(2026, 7, 19, 8, 30, tzinfo=timezone.utc)
    _schedule_billing(schedule, handle, deleted_at.isoformat())
    adapter = _BillingAdapter(
        [
            RunPodBilling(amount_usd=0.0, time_billed_ms=0, records=()),
            RunPodBilling(
                amount_usd=0.125,
                time_billed_ms=1_800_000,
                records=({"amount": 0.125, "timeBilledMs": 1_800_000},),
            ),
        ]
    )
    audit = InMemoryAuditLog()

    first = _run(
        reconcile_billing(
            schedule,
            "plus-1h",
            now=deleted_at + timedelta(hours=1),
            adapter=adapter,
            audit=audit,
        )
    )
    repeated = _run(
        reconcile_billing(
            schedule,
            "plus-1h",
            now=deleted_at + timedelta(hours=2),
            adapter=adapter,
            audit=audit,
        )
    )
    final = _run(
        reconcile_billing(
            schedule,
            "plus-6h",
            now=deleted_at + timedelta(hours=6),
            adapter=adapter,
            audit=audit,
        )
    )

    assert first["outcomes"][0]["status"] == "empty"
    assert repeated["outcomes"][0]["status"] == "already_attempted"
    assert final["outcomes"][0]["status"] == "confirmed"
    assert adapter.calls == 2
    assert [event.action for event in audit.events] == ["billing.confirmed"]
    saved = json.loads(schedule.read_text(encoding="utf-8"))
    assert saved["resources"][0]["slots"]["plus-6h"]["amount_usd"] == 0.125
    assert not schedule.with_suffix(".json.tmp").exists()


def test_wave_budget_uses_estimate_even_while_billing_is_pending() -> None:
    with pytest.raises(LiveExecutionError, match="wave budget reached"):
        _check_wave_budget(5.0)
