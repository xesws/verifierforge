"""RunPod deletion proof and delayed billing evidence for P-2."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

from app.provisioning.runpod import MANAGED_NAME_PREFIX
from core.provisioning_contracts import (
    ProvisionAuditEvent,
    ProvisionHandle,
    ProvisionProvider,
    ProvisionState,
)


DELETE_TIMEOUT_SECONDS = 30 * 60
DELETE_POLL_SECONDS = 30
BILLING_SLOTS = {
    "plus-1h": timedelta(hours=1),
    "plus-6h": timedelta(hours=6),
}


class TerminationEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeletionReceipt:
    external_id: str
    checked_at: str
    target_absent: bool
    vf_auto_prefix_count: int


def prefixed_pods(inventory: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        pod for pod in inventory
        if str(pod.get("name") or "").startswith(MANAGED_NAME_PREFIX)
    ]


async def confirm_deleted(
    adapter: Any,
    handle: ProvisionHandle,
    *,
    timeout_s: float = DELETE_TIMEOUT_SECONDS,
    poll_s: float = DELETE_POLL_SECONDS,
) -> DeletionReceipt:
    deadline = time.monotonic() + timeout_s
    target_present = True
    prefix_count = -1
    while True:
        pod = await adapter.get_pod(handle.external_id)
        prefix_count = len(prefixed_pods(await adapter.list_account_pods()))
        target_present = pod is not None
        if not target_present and prefix_count == 0:
            return DeletionReceipt(
                external_id=handle.external_id,
                checked_at=datetime.now(timezone.utc).isoformat(),
                target_absent=True,
                vf_auto_prefix_count=0,
            )
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(poll_s)
    raise TerminationEvidenceError(
        "RunPod deletion readback failed: "
        f"target_present={target_present}, vf_auto_prefix_count={prefix_count}"
    )


async def audit_deletion(
    audit: Any,
    handle: ProvisionHandle,
    receipt: DeletionReceipt,
) -> None:
    await audit.append(
        ProvisionAuditEvent(
            actor="p2-executor",
            job_id=handle.job_id,
            approval_id=handle.approval_id,
            action="provider.deletion_confirmed",
            provider=handle.provider,
            external_id=handle.external_id,
            before_state=ProvisionState.TERMINATED,
            after_state=ProvisionState.TERMINATED,
            reason="provider target absent and raw vf-auto prefix inventory is empty",
            detail={
                "checked_at": receipt.checked_at,
                "target_absent": receipt.target_absent,
                "vf_auto_prefix_count": receipt.vf_auto_prefix_count,
            },
        )
    )


def schedule_billing(
    path: Path,
    handle: ProvisionHandle,
    deleted_confirmed_at: str,
) -> None:
    path = Path(path)
    payload = read_json_object(path, "billing schedule") if path.exists() else {
        "schema_version": 1,
        "resources": [],
    }
    resources = payload.get("resources")
    if not isinstance(resources, list):
        raise TerminationEvidenceError("billing schedule has an invalid resources list")
    existing = next(
        (
            item for item in resources
            if isinstance(item, dict) and item.get("external_id") == handle.external_id
        ),
        None,
    )
    if existing is not None:
        expected = (handle.job_id, handle.approval_id)
        actual = (existing.get("job_id"), existing.get("approval_id"))
        if actual != expected:
            raise TerminationEvidenceError("billing schedule provider identity collision")
        return
    deleted_at = parse_timestamp(deleted_confirmed_at)
    resources.append(
        {
            "external_id": handle.external_id,
            "job_id": handle.job_id,
            "approval_id": handle.approval_id,
            "provider": handle.provider.value,
            "start_time": handle.created_at.astimezone(timezone.utc).isoformat(),
            "deleted_confirmed_at": deleted_at.isoformat(),
            "slots": {
                name: {
                    "due_at": (deleted_at + delay).isoformat(),
                    "status": "scheduled",
                }
                for name, delay in BILLING_SLOTS.items()
            },
        }
    )
    atomic_write_json(path, payload)


async def reconcile_billing_schedule(
    path: Path,
    slot: str,
    *,
    adapter: Any,
    audit: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    if slot not in BILLING_SLOTS:
        raise TerminationEvidenceError(f"unknown billing slot: {slot}")
    path = Path(path)
    payload = read_json_object(path, "billing schedule")
    resources = payload.get("resources")
    if not isinstance(resources, list):
        raise TerminationEvidenceError("billing schedule has an invalid resources list")
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    outcomes: list[dict[str, Any]] = []
    for resource in resources:
        if not isinstance(resource, dict):
            raise TerminationEvidenceError("billing schedule contains a non-object resource")
        slots = resource.get("slots")
        if not isinstance(slots, dict) or not isinstance(slots.get(slot), dict):
            raise TerminationEvidenceError("billing schedule is missing the requested slot")
        attempt = slots[slot]
        if attempt.get("status") != "scheduled":
            outcomes.append(
                {"external_id": resource.get("external_id"), "status": "already_attempted"}
            )
            continue
        due_at = parse_timestamp(str(attempt.get("due_at", "")))
        if observed_at < due_at:
            outcomes.append(
                {
                    "external_id": resource.get("external_id"),
                    "status": "not_due",
                    "due_at": due_at.isoformat(),
                }
            )
            continue
        attempt["attempted_at"] = observed_at.isoformat()
        try:
            billing = await adapter.billing(
                str(resource["external_id"]),
                start_time=parse_timestamp(str(resource["start_time"])),
            )
        except Exception as error:
            attempt.update(
                {
                    "status": "error",
                    "error_type": type(error).__name__,
                    "error": str(error)[:2000],
                }
            )
            outcomes.append(
                {
                    "external_id": resource.get("external_id"),
                    "status": "error",
                    "error_type": type(error).__name__,
                }
            )
            atomic_write_json(path, payload)
            continue
        had_confirmed = any(
            isinstance(value, dict) and value.get("status") == "confirmed"
            for name, value in slots.items()
            if name != slot
        )
        status = "confirmed" if billing.records else (
            "unresolved" if slot == "plus-6h" else "empty"
        )
        attempt.update(
            {
                "status": status,
                "record_count": len(billing.records),
                "amount_usd": billing.amount_usd if billing.records else None,
                "time_billed_ms": billing.time_billed_ms if billing.records else None,
            }
        )
        if billing.records and not had_confirmed:
            handle = ProvisionHandle(
                provider=ProvisionProvider.RUNPOD,
                external_id=str(resource["external_id"]),
                job_id=str(resource["job_id"]),
                approval_id=str(resource["approval_id"]),
                created_at=parse_timestamp(str(resource["start_time"])),
            )
            try:
                await audit_billing(
                    audit,
                    handle,
                    billing.amount_usd,
                    billing.time_billed_ms,
                )
            except Exception as error:
                attempt["audit_error"] = f"{type(error).__name__}: {str(error)[:1000]}"
        outcomes.append(
            {
                "external_id": resource.get("external_id"),
                "status": status,
                "record_count": len(billing.records),
                "amount_usd": billing.amount_usd if billing.records else None,
            }
        )
        atomic_write_json(path, payload)
    return {"slot": slot, "observed_at": observed_at.isoformat(), "outcomes": outcomes}


async def audit_billing(
    audit: Any,
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
            reason="provider billing receipt returned",
            detail={"amount_usd": amount_usd, "time_billed_ms": time_billed_ms},
        )
    )


def parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TerminationEvidenceError(f"invalid evidence timestamp: {value!r}") from error
    if parsed.tzinfo is None:
        raise TerminationEvidenceError(f"evidence timestamp has no timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TerminationEvidenceError(f"{label} is unreadable") from error
    if not isinstance(value, dict):
        raise TerminationEvidenceError(f"{label} is not a JSON object")
    return value


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


__all__ = [
    "BILLING_SLOTS",
    "DeletionReceipt",
    "TerminationEvidenceError",
    "audit_deletion",
    "confirm_deleted",
    "prefixed_pods",
    "reconcile_billing_schedule",
    "schedule_billing",
]
