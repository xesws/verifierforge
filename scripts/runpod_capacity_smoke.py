"""Prove live RunPod capacity selection with one immediate-delete Pod."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv

from app.provisioning.runpod import MANAGED_NAME_PREFIX, RUNPOD_IMAGE, RunPodAdapter
from core.provisioning_contracts import GPUClass, ProvisionProvider, ProvisionSpec


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / "runs" / "provisioning" / "v0.32.0" / "capacity-live.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _spec(public_key: str, *, budget_cap_usd: float, max_live_minutes: int) -> ProvisionSpec:
    suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return ProvisionSpec(
        job_id=f"capacity-{suffix}",
        approval_id=f"capacity-proof-{suffix}",
        requested_by="p2-5-capacity-smoke",
        provider=ProvisionProvider.RUNPOD,
        gpu_class=GPUClass.SMALL_ADA,
        image=RUNPOD_IMAGE,
        container_disk_gb=20,
        env={"VF_PROVISION_STAGE": "p2_5_capacity_smoke"},
        ports=[22],
        ssh_pubkey=public_key,
        budget_usd_cap=budget_cap_usd,
        max_runtime_min=max_live_minutes,
    )


async def _wait_absent(
    adapter: RunPodAdapter,
    external_id: str,
    *,
    timeout_s: float = 60.0,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await adapter.get_pod(external_id) is None:
            return True
        await asyncio.sleep(5)
    return False


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    key = os.environ.get("RUNPOD_API_KEY", "").strip()
    if not key:
        raise SystemExit("missing required environment variable: RUNPOD_API_KEY")
    public_key_path = Path(args.ssh_public_key).expanduser()
    public_key = public_key_path.read_text().strip()
    spec = _spec(
        public_key,
        budget_cap_usd=args.budget_cap_usd,
        max_live_minutes=args.max_live_minutes,
    )
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    handle = None
    evidence: dict[str, Any] = {
        "version": "v0.32.0",
        "started_at": started_at,
        "budget_cap_usd": args.budget_cap_usd,
        "max_live_minutes": args.max_live_minutes,
        "status": "started",
    }

    async with RunPodAdapter(api_key=key, create_timeout_s=30) as adapter:
        before = await adapter.list_account_pods()
        prefixed_before = [
            pod
            for pod in before
            if str(pod.get("name", "")).startswith(MANAGED_NAME_PREFIX)
        ]
        if prefixed_before:
            raise SystemExit("preflight found an existing vf-auto-* resource")

        offers = await adapter.available_gpu_offers(spec)
        if not offers:
            raise SystemExit("no_capacity: no approved live offer")
        cheapest = offers[0]
        reserved_cost = cheapest.hourly_price_usd * args.max_live_minutes / 60
        evidence["availability"] = [
            {
                "gpu_model": offer.gpu_type_id,
                "display_name": offer.display_name,
                "cloud_type": offer.cloud_type,
                "hourly_price_usd": offer.hourly_price_usd,
                "stock_status": offer.stock_status,
            }
            for offer in offers
        ]
        evidence["preflight"] = {
            "account_pod_count": len(before),
            "vf_auto_prefix_count": 0,
            "cheapest_reserved_cost_usd": round(reserved_cost, 6),
        }
        if reserved_cost > args.budget_cap_usd:
            raise SystemExit(
                "capacity smoke conservative reservation exceeds the budget cap"
            )

        try:
            handle = await adapter.provision(spec)
            evidence["selected"] = {
                "external_id": handle.external_id,
                "gpu_model": handle.labels["gpu_model"],
                "display_name": handle.labels["gpu_display_name"],
                "cloud_type": handle.labels["cloud_type"],
                "hourly_price_usd": float(handle.labels["hourly_price_usd"]),
            }
        finally:
            if handle is not None:
                await adapter.terminate(handle)

        if handle is None:
            raise SystemExit("capacity smoke did not receive a provision handle")
        target_absent = await _wait_absent(adapter, handle.external_id)
        after = await adapter.list_account_pods()
        prefixed_after = [
            pod
            for pod in after
            if str(pod.get("name", "")).startswith(MANAGED_NAME_PREFIX)
        ]
        if not target_absent or prefixed_after:
            raise SystemExit("cleanup gate failed: target or vf-auto-* resource remains")

    elapsed_seconds = time.monotonic() - started_monotonic
    selected_price = float(evidence["selected"]["hourly_price_usd"])
    evidence.update(
        {
            "finished_at": _utc_now(),
            "elapsed_seconds": round(elapsed_seconds, 3),
            "estimated_cost_usd": round(selected_price * elapsed_seconds / 3600, 6),
            "cleanup": {
                "target_get_absent": True,
                "vf_auto_prefix_count": 0,
            },
            "status": "passed",
        }
    )
    _write_json_atomic(args.evidence, evidence)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create and immediately delete one capacity-selected RunPod Pod."
    )
    parser.add_argument("--budget-cap-usd", type=float, default=1.0)
    parser.add_argument("--max-live-minutes", type=int, default=5)
    parser.add_argument("--ssh-public-key", default="~/.ssh/id_ed25519.pub")
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()
    if args.budget_cap_usd <= 0 or args.budget_cap_usd > 1:
        raise SystemExit("--budget-cap-usd must be in (0, 1]")
    if args.max_live_minutes < 1 or args.max_live_minutes > 5:
        raise SystemExit("--max-live-minutes must be in [1, 5]")
    load_dotenv(ROOT / ".env", override=False)
    result = asyncio.run(_run(args))
    print(
        json.dumps(
            {
                "status": result["status"],
                "selected": result["selected"],
                "estimated_cost_usd": result["estimated_cost_usd"],
                "cleanup": result["cleanup"],
                "evidence": str(args.evidence),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
