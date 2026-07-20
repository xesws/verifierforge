from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from app.provisioning.errors import ProvisionProviderError
from app.provisioning.runpod import (
    OWNER_MARKER_KEY,
    OWNER_MARKER_VALUE,
    RUNPOD_IMAGE,
    RunPodAdapter,
)
from core.provisioning_contracts import (
    GPUClass,
    ProvisionHandle,
    ProvisionProvider,
    ProvisionSpec,
    ProvisionState,
)


def _run(coro):
    return asyncio.run(coro)


def _spec() -> ProvisionSpec:
    return ProvisionSpec(
        job_id="p2-job-1",
        approval_id="approval-1",
        requested_by="owner",
        provider=ProvisionProvider.RUNPOD,
        gpu_class=GPUClass.SMALL_ADA,
        image=RUNPOD_IMAGE,
        container_disk_gb=80,
        env={"VF_STORAGE_BACKEND": "s3"},
        ports=[22],
        ssh_pubkey="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestOnly owner",
        budget_usd_cap=5,
        max_runtime_min=180,
    )


def _pod(**overrides):
    value = {
        "id": "pod-1",
        "name": "vf-auto-p2-job-1",
        "desiredStatus": "RUNNING",
        "createdAt": "2026-07-19T12:00:00Z",
        "publicIp": "203.0.113.5",
        "portMappings": {"22": 12022},
        "costPerHr": 0.2,
        "runtime": {"uptimeInSeconds": 900},
        "env": {
            OWNER_MARKER_KEY: OWNER_MARKER_VALUE,
            "VF_JOB_ID": "p2-job-1",
            "VF_APPROVAL_ID": "approval-1",
        },
    }
    value.update(overrides)
    return value


def test_create_status_delete_and_billing_contract() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["gpuCount"] == 1
            assert payload["gpuTypeIds"] == [
                "NVIDIA RTX 2000 Ada Generation",
                "NVIDIA RTX 4000 SFF Ada Generation",
                "NVIDIA RTX 4000 Ada Generation",
                "NVIDIA L4",
            ]
            assert payload["volumeInGb"] == 0
            assert "networkVolumeId" not in payload
            assert payload["env"][OWNER_MARKER_KEY] == OWNER_MARKER_VALUE
            return httpx.Response(201, json=_pod())
        if request.url.path.endswith("/billing/pods"):
            return httpx.Response(
                200,
                json={"records": [{"amount": 0.05, "timeBilledMs": 900000}]},
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json=_pod())

    async def scenario() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = RunPodAdapter("secret-test-key", client=client)
        handle = await adapter.provision(_spec())
        status = await adapter.status(handle)
        assert status.state == ProvisionState.BOOTSTRAPPING
        assert status.ssh == "root@203.0.113.5:12022"
        assert status.cost_accrued_usd == 0.05
        billing = await adapter.billing(
            handle.external_id, start_time=datetime(2026, 7, 19, tzinfo=timezone.utc)
        )
        assert billing.amount_usd == 0.05
        assert billing.time_billed_ms == 900000
        await adapter.terminate(handle)
        await client.aclose()

    _run(scenario())
    assert all(request.headers["Authorization"] == "Bearer secret-test-key" for request in calls)


def test_create_retries_secure_only_for_explicit_community_capacity_error() -> None:
    clouds: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        clouds.append(payload["cloudType"])
        if payload["cloudType"] == "COMMUNITY":
            return httpx.Response(
                500,
                json={"error": "create pod: There are no instances currently available"},
            )
        return httpx.Response(201, json=_pod(cloudType="SECURE"))

    async def scenario() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = RunPodAdapter("key", client=client)
        handle = await adapter.provision(_spec())
        assert handle.external_id == "pod-1"
        await client.aclose()

    _run(scenario())
    assert clouds == ["COMMUNITY", "SECURE"]


def test_create_does_not_retry_secure_for_non_capacity_error() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, text="provider denied")

    async def scenario() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = RunPodAdapter("key", client=client)
        with pytest.raises(ProvisionProviderError, match="HTTP 403"):
            await adapter.provision(_spec())
        await client.aclose()

    _run(scenario())
    assert calls == 1


def test_list_active_requires_prefix_and_owner_marker() -> None:
    foreign = _pod(id="foreign", name="owner-pod")
    prefix_only = _pod(id="prefix-only", env={"VF_JOB_ID": "p2-job-1"})
    managed = _pod(id="managed")

    async def scenario() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=[foreign, prefix_only, managed]))
        )
        adapter = RunPodAdapter("key", client=client)
        handles = await adapter.list_active()
        assert [handle.external_id for handle in handles] == ["managed"]
        await client.aclose()

    _run(scenario())


def test_raw_inventory_preserves_prefix_only_and_terminal_pods() -> None:
    prefix_only = _pod(
        id="prefix-only",
        desiredStatus="EXITED",
        env={"VF_JOB_ID": "p2-job-1"},
    )
    managed = _pod(id="managed")

    async def scenario() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json=[prefix_only, managed])
            )
        )
        adapter = RunPodAdapter("key", client=client)
        inventory = await adapter.list_account_pods()
        active = await adapter.list_active()
        assert [pod["id"] for pod in inventory] == ["prefix-only", "managed"]
        assert [handle.external_id for handle in active] == ["managed"]
        await client.aclose()

    _run(scenario())


def test_status_refreshes_ssh_and_refuses_foreign_delete() -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if request.method == "GET":
            return httpx.Response(200, json=_pod(publicIp=f"203.0.113.{count}"))
        raise AssertionError("foreign delete must not be sent")

    async def scenario() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = RunPodAdapter("key", client=client)
        handle = ProvisionHandle(
            provider="runpod",
            external_id="pod-1",
            job_id="wrong-job",
            approval_id="approval-1",
        )
        with pytest.raises(ProvisionProviderError, match="ownership"):
            await adapter.terminate(handle)
        await client.aclose()

    _run(scenario())


def test_status_derives_uptime_and_cost_from_runpod_utc_timestamp() -> None:
    started = datetime.now(timezone.utc) - timedelta(minutes=2)
    provider_timestamp = (
        started.strftime("%Y-%m-%d %H:%M:%S.")
        + f"{started.microsecond // 10_000:02d} +0000 UTC"
    )

    async def scenario() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json=_pod(runtime=None, lastStartedAt=provider_timestamp, costPerHr=0.39),
                )
            )
        )
        adapter = RunPodAdapter("key", client=client)
        handle = ProvisionHandle(
            provider=ProvisionProvider.RUNPOD,
            external_id="pod-1",
            job_id="p2-job-1",
            approval_id="approval-1",
        )
        status = await adapter.status(handle)
        assert status.uptime_min >= 2
        assert status.cost_accrued_usd >= 0.012
        await client.aclose()

    _run(scenario())


def test_http_error_preserves_status_and_bounded_provider_body() -> None:
    async def scenario() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(403, text="provider denied")
            )
        )
        adapter = RunPodAdapter("do-not-echo", client=client)
        with pytest.raises(ProvisionProviderError) as captured:
            await adapter.list_account_pods()
        message = str(captured.value)
        assert "HTTP 403" in message
        assert "provider denied" in message
        assert "do-not-echo" not in message
        await client.aclose()

    _run(scenario())


def test_credential_provider_is_resolved_per_http_call_and_redacted() -> None:
    async def scenario() -> None:
        calls = 0

        def credential() -> str:
            nonlocal calls
            calls += 1
            return "rotating-secret"

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    403, text="provider echoed rotating-secret"
                )
            )
        )
        adapter = RunPodAdapter(api_key_provider=credential, client=client)
        for _ in range(2):
            with pytest.raises(ProvisionProviderError) as captured:
                await adapter.list_account_pods()
            assert "rotating-secret" not in str(captured.value)
            assert "[REDACTED]" in str(captured.value)
        assert calls == 2
        await client.aclose()

    _run(scenario())
