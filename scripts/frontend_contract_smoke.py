#!/usr/bin/env python3
"""Exercise the public frontend contract without revealing runtime secrets."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.settings import DatabaseSettings


BASE_URL = os.getenv(
    "VF_PUBLIC_API_URL", "https://verifierforge-production.up.railway.app"
).rstrip("/")
CLUSTER = "data-pull-sql"
USER_ID = f"v035-smoke-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"


def request(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    invite = os.getenv("VF_REVIEW_INVITE_CODE", "")
    if not invite:
        raise RuntimeError("VF_REVIEW_INVITE_CODE is required")
    token = base64.b64encode(f"judge:{invite}".encode()).decode()
    data = None if body is None else json.dumps(body).encode()
    call = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(call, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def checked(method: str, path: str, expected: set[int], body: dict[str, Any] | None = None) -> Any:
    status, payload = request(method, path, body)
    verdict = "ok" if status in expected else "FAIL"
    keys = ",".join(sorted(payload)[:8]) if isinstance(payload, dict) else f"items={len(payload)}"
    print(f"{verdict:4} {method:4} {path:62} HTTP {status} fields={keys}")
    if status not in expected:
        detail = payload.get("detail", "unexpected response") if isinstance(payload, dict) else "unexpected response"
        raise RuntimeError(f"{method} {path}: HTTP {status}: {str(detail)[:300]}")
    return payload


async def cleanup(job_id: str | None) -> None:
    settings = DatabaseSettings.from_env()
    engine = create_async_engine(settings.url, pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM provider_credentials WHERE user_id = :user_id"), {"user_id": USER_ID})
            if job_id:
                await connection.execute(text("DELETE FROM jobs WHERE job_id = :job_id"), {"job_id": job_id})
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-wake", action="store_true", help="consume the separately authorized paid Wake")
    args = parser.parse_args()
    created_job: str | None = None
    try:
        checked("GET", "/jobs", {200})
        job = checked("POST", "/jobs", {201}, {"template": "v035-ui-smoke", "model": "Qwen/Qwen2.5-1.5B-Instruct"})
        created_job = job["job_id"]
        checked("GET", f"/jobs/{created_job}", {200})
        checked("GET", f"/jobs/{created_job}/metrics", {200})
        checked("GET", "/clusters", {200})
        checked("GET", f"/clusters/{CLUSTER}", {200})
        analysis = checked("POST", f"/clusters/{CLUSTER}/agent/analyze", {200}, {"force_refresh": False})
        decision_id = analysis["decision_id"]
        checked("GET", f"/clusters/{CLUSTER}/agent/decision", {200})
        approval = checked("POST", f"/agent-decisions/{decision_id}/approvals", {200}, {"approved_by": "judge"})
        checked("GET", f"/agent-decisions/{decision_id}/approval", {200})
        checked("POST", f"/approvals/{approval['approval_id']}/start-forge", {404}, {"requested_by": "judge", "confirm_provider_spend": True})
        checked("GET", f"/approvals/{approval['approval_id']}/forge-execution", {200, 404})
        route = checked("GET", f"/clusters/{CLUSTER}/routing", {200})
        checked("PUT", f"/clusters/{CLUSTER}/routing", {200}, route)
        checked("GET", f"/clusters/{CLUSTER}/live-pass-rate", {200})
        source = checked("GET", f"/clusters/{CLUSTER}/sample-source", {200})
        source_body = {"uri": source["uri"], "approved_by": "judge", "expected_sha256": source["sha256"], "expected_row_count": source["row_count"]}
        checked("PUT", f"/clusters/{CLUSTER}/sample-source", {200}, source_body)
        checked("GET", f"/settings/provider-credentials/runpod?user_id={USER_ID}", {200})
        checked("PUT", "/settings/provider-credentials/runpod", {200}, {"user_id": USER_ID, "api_key": "v035-fixture-not-a-provider-key"})
        if args.include_wake:
            checked("POST", "/serving/wake", {200, 202}, {"model_id": "vf-demo", "confirm_provider_spend": True})
        else:
            print("skip POST /serving/wake (paid operation requires --include-wake)")
        serving = checked("GET", "/serving/status?model_id=vf-demo", {200})
        probe_body = {
            "model": "vf-demo",
            "messages": [{"role": "user", "content": "Return SELECT 1"}],
        }
        if serving.get("state") == "cold":
            checked("POST", "/serving/tuned-completion", {409}, probe_body)
        elif args.include_wake:
            checked("POST", "/serving/tuned-completion", {200}, probe_body)
        else:
            print("skip POST /serving/tuned-completion (ready endpoint requires --include-wake)")
        return 0
    finally:
        try:
            asyncio.run(cleanup(created_job))
            print("cleanup smoke job + credential: ok")
        except Exception as error:
            print(f"cleanup failed safely: {type(error).__name__}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
