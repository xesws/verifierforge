"""Run the bounded v0.27 production Analyze evidence sequence exactly once."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


MODEL = "gpt-5.6-luna"
CLUSTER_ID = "data-pull-sql"
SOURCE_URI = "data/nl2sql/v0.10.0-training-pool.jsonl"
SOURCE_SHA256 = "c97a5adea789fae3be249bc9ac95a1902ae5a9769de9eefbc08277f056878e8c"
RESERVATION_USD = 0.25
EXPECTED_CONFIG = {
    "base_model": "Qwen/Qwen2.5-0.5B-Instruct",
    "steps": 100,
    "k": 8,
    "checkpoint_interval": 50,
    "budget_usd_cap": 5.0,
    "provider_pref": "runpod",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preserve no-source and approved-source product Agent evidence."
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path("runs/forge-agent/llm-cost.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runs/forge-agent/v0.27.0-product-live.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(override=False)
    _configure_production_environment()

    from fastapi.testclient import TestClient

    from app.api.main import app
    from app.db import repository_gateway, run_migrations
    from app.db.records import ClusterRecord
    from app.db.settings import DatabaseSettings
    from app.gpt.budget import CostLedger
    from app.proxy.clusters import cluster_profile

    settings = DatabaseSettings.from_env()
    run_migrations(settings)
    gateway = repository_gateway(settings)
    profile = cluster_profile(CLUSTER_ID)

    async def clear_source(repositories):
        existing = await repositories.clusters.get(CLUSTER_ID)
        base = existing or ClusterRecord(
            cluster_id=profile.cluster_id,
            name=profile.name,
            status=profile.status.value,
            monthly_calls=profile.monthly_calls,
            monthly_cost_usd=profile.monthly_cost_usd,
            trainable=profile.trainable,
            job_id=profile.job_id,
            analyzer_summary=None,
            updated_at=datetime.now(timezone.utc),
        )
        return await repositories.clusters.put(
            replace(
                base,
                approved_sample_source=None,
                updated_at=datetime.now(timezone.utc),
            )
        )

    gateway.call(clear_source)
    client = TestClient(app)
    ledger = CostLedger(args.ledger)
    evidence: dict[str, Any] = {
        "version": "v0.27.0",
        "cluster_id": CLUSTER_ID,
        "model": MODEL,
        "new_spend_cap_usd": RESERVATION_USD * 2,
        "runs": [],
    }

    first = _run_once(client, ledger, phase="no-approved-source")
    evidence["runs"].append(first)
    if first["decision"] != "need_more_data":
        _write(args.output, evidence)
        raise RuntimeError("source-less production Analyze did not return need_more_data")

    source_response = client.put(
        f"/clusters/{CLUSTER_ID}/sample-source",
        json={
            "uri": SOURCE_URI,
            "approved_by": "owner",
            "expected_sha256": SOURCE_SHA256,
            "expected_row_count": 50,
        },
    )
    if source_response.status_code != 200:
        _write(args.output, evidence)
        raise RuntimeError("approved sample source attachment failed")
    source = source_response.json()
    evidence["approved_sample_source"] = {
        key: source[key]
        for key in ("kind", "uri", "sha256", "row_count", "approved_by", "approved_at")
    }

    second = _run_once(client, ledger, phase="approved-source")
    evidence["runs"].append(second)
    if second["decision"] != "forge" or second["config"] != EXPECTED_CONFIG:
        _write(args.output, evidence)
        raise RuntimeError("approved-source production Analyze did not return the legal P2 config")

    approval = client.post(
        f"/agent-decisions/{second['decision_id']}/approvals",
        json={"approved_by": "owner"},
    )
    if approval.status_code != 200:
        _write(args.output, evidence)
        raise RuntimeError("production forge decision approval failed")
    receipt = approval.json()
    evidence["approval"] = {
        key: receipt[key]
        for key in ("approval_id", "decision_id", "approved_by", "approved_at")
    }
    evidence["completed_at"] = datetime.now(timezone.utc).isoformat()
    _write(args.output, evidence)
    print(
        json.dumps(
            {
                "status": "complete",
                "first_decision": first["decision"],
                "second_decision": second["decision"],
                "approval_id": receipt["approval_id"],
                "new_reservation_usd": RESERVATION_USD * 2,
                "evidence": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0


def _run_once(client, ledger, *, phase: str) -> dict[str, Any]:
    ledger.authorize("openai", RESERVATION_USD)
    response = client.post(
        f"/clusters/{CLUSTER_ID}/agent/analyze",
        json={"execution_profile": "p2_gate_b", "force_refresh": True},
    )
    if response.status_code != 200:
        ledger.record(
            provider="openai",
            reservation_usd=RESERVATION_USD,
            provider_reported_cost_usd=None,
            model=MODEL,
            input_tokens=0,
            output_tokens=0,
            status=f"v0.27-product-{phase}-failed",
        )
        raise RuntimeError(f"production Analyze phase {phase} failed")
    payload = response.json()

    from app.api import agent as agent_api

    summary = agent_api._stores().decisions.get(payload["decision_id"])
    if summary is None:
        raise RuntimeError("production Analyze summary was not persisted")
    ledger.record(
        provider="openai",
        reservation_usd=RESERVATION_USD,
        provider_reported_cost_usd=None,
        model=MODEL,
        input_tokens=summary.total_input_tokens,
        output_tokens=summary.total_output_tokens,
        status=f"v0.27-product-{phase}-success",
    )
    return {
        "phase": phase,
        "decision_id": payload["decision_id"],
        "trace_s3_key": summary.trace_s3_key,
        "decision": payload["decision"]["decision"],
        "rationale": payload["decision"]["rationale"],
        "confidence": payload["decision"]["confidence"],
        "config": payload["decision"]["config"],
        "input_tokens": summary.total_input_tokens,
        "output_tokens": summary.total_output_tokens,
        "reservation_usd": RESERVATION_USD,
    }


def _configure_production_environment() -> None:
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise RuntimeError("OPENAI_API_KEY is required")
    if not os.environ.get("SUPABASE_DB_URL", "").strip():
        raise RuntimeError("SUPABASE_DB_URL is required")
    os.environ.pop("VF_LLM_API_KEY", None)
    os.environ.update(
        {
            "VF_DB_BACKEND": "postgres",
            "VF_LLM_PROVIDER": "openai",
            "VF_LLM_BASE_URL": "https://api.openai.com/v1",
            "VF_LLM_MODEL": MODEL,
            "VF_AGENT_ENABLED": "true",
            "VF_AGENT_BINDING": "real",
            "VF_AGENT_GATE_C_PASSED": "true",
            "VF_AGENT_MAX_TRAINING_BUDGET_USD": "5",
        }
    )


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


if __name__ == "__main__":
    raise SystemExit(main())

