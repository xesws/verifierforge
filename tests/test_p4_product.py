from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from app.db import repository_gateway
from app.db.settings import DatabaseSettings
from app.provisioning.product import CredentialResolver, training_config_to_spec
from app.provisioning.runpod import RunPodAdapter
from core.agent_contracts import TrainingConfig
from core.provisioning_contracts import ProvisionProvider


def test_training_config_translation_takes_lower_budget_and_has_no_secret_env() -> None:
    config = TrainingConfig(budget_usd_cap=4.0, provider_pref="auto")
    spec = training_config_to_spec(
        config,
        approval_id="approval-1",
        job_id="forge-1",
        requested_by="owner",
        system_budget_cap=1.0,
        environ={
            "VF_PROVISION_SSH_PUBLIC_KEY": "ssh-ed25519 " + "a" * 48,
        },
    )

    assert spec.provider is ProvisionProvider.RUNPOD
    assert spec.budget_usd_cap == 1.0
    assert spec.env["VF_TRAINING_STEPS"] == "400"
    assert not any("KEY" in key or "SECRET" in key for key in spec.env)


def test_system_fallback_is_read_at_each_provider_request(
    tmp_path: Path,
) -> None:
    gateway = repository_gateway(DatabaseSettings.sqlite(tmp_path / "resolver.sqlite3"))
    environ = {"RUNPOD_API_KEY": "first-key"}
    resolver = CredentialResolver(
        gateway=gateway,
        user_id="owner",
        provider=ProvisionProvider.RUNPOD,
        environ=environ,
    )
    observed: list[str] = []

    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            observed.append(request.headers["Authorization"])
            return httpx.Response(200, json=[])

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = RunPodAdapter(api_key_provider=resolver, client=client)
        await adapter.list_account_pods()
        environ["RUNPOD_API_KEY"] = "second-key"
        await adapter.list_account_pods()
        await client.aclose()

    asyncio.run(scenario())
    assert observed == ["Bearer first-key", "Bearer second-key"]
