"""Run one approval→Start Forge→RunPod readiness→delete P-4 smoke."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from app.api.main import app
from app.db import repository_gateway
from app.provisioning.product import CredentialResolver
from app.provisioning.runpod import MANAGED_NAME_PREFIX, RunPodAdapter
from core.p4_contracts import ForgeExecutionStatus
from core.provisioning_contracts import ProvisionProvider


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "runs" / "provisioning" / "v0.29.0" / "p4-live"
EVIDENCE_PATH = EVIDENCE_DIR / "lifecycle.json"
BILLING_PATH = EVIDENCE_DIR / "billing-schedule.json"
P2_PRIOR_ESTIMATE_USD = 2.363580
P4_CAP_USD = 1.0
WAVE_CAP_USD = 5.0


def _configure() -> None:
    load_dotenv(ROOT / ".env", override=False)
    if not os.environ.get("RUNPOD_API_KEY", "").strip():
        raise SystemExit("P-4 live smoke is missing required provider configuration")
    if P2_PRIOR_ESTIMATE_USD + P4_CAP_USD > WAVE_CAP_USD:
        raise SystemExit("P-4 conservative reservation exceeds the cumulative fuse")
    os.environ.update(
        {
            "VF_AGENT_ENABLED": "true",
            "VF_AGENT_BINDING": "mock",
            "VF_AUTOPROVISION": "true",
            "VF_PROVISION_BINDING": "runpod",
            "VF_PROVISION_SYSTEM_BUDGET_USD_CAP": str(P4_CAP_USD),
            "VF_PROVISION_MAX_RUNTIME_MIN": "15",
            "VF_P4_POLL_SECONDS": "5",
            "VF_P4_READY_TIMEOUT_SECONDS": "600",
            "VF_P4_DELETE_TIMEOUT_SECONDS": "180",
            "VF_P4_BILLING_SCHEDULE": str(BILLING_PATH),
        }
    )


async def _preflight() -> dict[str, object]:
    gateway = repository_gateway()
    resolver = CredentialResolver(
        gateway=gateway,
        user_id="p4-live-owner",
        provider=ProvisionProvider.RUNPOD,
    )
    async with RunPodAdapter(api_key_provider=resolver) as adapter:
        pods = await adapter.list_account_pods()
    prefixed = [
        pod for pod in pods if str(pod.get("name", "")).startswith(MANAGED_NAME_PREFIX)
    ]
    if prefixed:
        raise SystemExit("P-4 preflight found an existing vf-auto-* resource")
    return {
        "account_pod_count": len(pods),
        "vf_auto_prefix_count": 0,
        "credential_source": resolver.source().value,
        "conservative_prior_estimate_usd": P2_PRIOR_ESTIMATE_USD,
        "reservation_usd": P4_CAP_USD,
        "cumulative_reserved_usd": P2_PRIOR_ESTIMATE_USD + P4_CAP_USD,
    }


def _run_api_chain() -> dict[str, object]:
    client = TestClient(app)
    analysis = client.post(
        "/clusters/data-pull-sql/agent/analyze",
        json={"execution_profile": "p2_gate_b", "force_refresh": True},
    )
    if analysis.status_code != 200:
        raise SystemExit(f"P-4 mock analysis failed with HTTP {analysis.status_code}")
    decision = analysis.json()
    approval = client.post(
        f"/agent-decisions/{decision['decision_id']}/approvals",
        json={"approved_by": "p4-live-owner"},
    )
    if approval.status_code != 200:
        raise SystemExit(f"P-4 approval failed with HTTP {approval.status_code}")
    approval_body = approval.json()
    started_at = datetime.now(timezone.utc)
    started = client.post(
        f"/approvals/{approval_body['approval_id']}/start-forge",
        json={
            "requested_by": "p4-live-owner",
            "confirm_provider_spend": True,
        },
    )
    if started.status_code != 200:
        raise SystemExit(f"P-4 Start Forge failed with HTTP {started.status_code}")
    accepted = ForgeExecutionStatus.model_validate(started.json())
    final_response = client.get(
        f"/approvals/{approval_body['approval_id']}/forge-execution"
    )
    if final_response.status_code != 200:
        raise SystemExit("P-4 final execution status is unavailable")
    final = ForgeExecutionStatus.model_validate(final_response.json())
    if final.state.value != "done":
        raise SystemExit(f"P-4 live smoke did not complete: {final.state.value}")
    gateway = repository_gateway()
    events = gateway.call(
        lambda repositories: repositories.provision_audit.list_for_approval(
            approval_body["approval_id"]
        )
    )
    actions = [event.action for event in events]
    required = {
        "provision.requested",
        "provision.created",
        "lifecycle.terminated",
        "provider.deletion_confirmed",
    }
    if not required.issubset(actions):
        raise SystemExit("P-4 provider audit is incomplete")
    return {
        "decision_id": decision["decision_id"],
        "approval_id": approval_body["approval_id"],
        "approval_only_response": {
            "approved_by": approval_body["approved_by"],
            "approved_at": approval_body["approved_at"],
        },
        "start_confirmed": True,
        "accepted_state": accepted.state.value,
        "final": final.model_dump(mode="json"),
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "audit_actions": actions,
        "billing_status": "scheduled_plus_1h_plus_6h",
    }


def main() -> None:
    _configure()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    preflight = asyncio.run(_preflight())
    chain = _run_api_chain()
    payload = {
        "schema_version": 1,
        "stage": "provisioner-p4-live",
        "status": "passed",
        "preflight": preflight,
        **chain,
    }
    temporary = EVIDENCE_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, EVIDENCE_PATH)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "approval_id": payload["approval_id"],
                "state": payload["final"]["state"],
                "cost_accrued_usd": payload["final"]["cost_accrued_usd"],
                "billing_status": payload["billing_status"],
                "evidence": str(EVIDENCE_PATH.relative_to(ROOT)),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
