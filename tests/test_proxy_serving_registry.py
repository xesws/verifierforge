from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from app.db.credentials import CredentialCipher, CredentialService
from app.db.gateway import RepositoryGateway
from app.db.records import ServingEndpointRecord
from app.db.settings import DatabaseSettings
from app.proxy.serving_registry import RegistryTunedResolver
from app.proxy.main import ProxySettings, forward_tuned_completion
from app.proxy.clusters import SYSTEM_PROMPTS_BY_CLUSTER, system_prompt_hash
from app.proxy.upstream import ForwardedResponse


NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


def test_registry_cache_holds_only_descriptor_and_decrypts_per_request(tmp_path: Path) -> None:
    gateway = RepositoryGateway(DatabaseSettings.sqlite(tmp_path / "proxy.sqlite3"))
    key = Fernet.generate_key().decode()
    environ = {"VF_CRED_KEY": key}
    clock = [0.0]
    try:
        async def seed(repositories):
            service = CredentialService(repositories.credentials, CredentialCipher(key))
            credential_id = await service.put(
                user_id="sv-session",
                provider="vllm-endpoint",
                value="endpoint-secret",
            )
            await repositories.serving_endpoints.put(
                ServingEndpointRecord(
                    model_id="vf-demo",
                    session_id="sv-session",
                    state="ready",
                    url="https://model.trycloudflare.com/v1",
                    api_key_ref=credential_id,
                    updated_at=NOW,
                )
            )
        gateway.call(seed)
        resolver = RegistryTunedResolver(
            lambda: gateway,
            environ=environ,
            clock=lambda: clock[0],
        )
        first = resolver.resolve()
        assert first is not None
        assert first.url == "https://model.trycloudflare.com/v1"
        assert first.api_key == "endpoint-secret"
        assert "endpoint-secret" not in repr(first)
        assert "endpoint-secret" not in repr(resolver._descriptor)

        async def make_cold(repositories):
            current = await repositories.serving_endpoints.get("vf-demo")
            assert current is not None
            await repositories.serving_endpoints.put(
                ServingEndpointRecord(
                    **{
                        **current.__dict__,
                        "state": "cold",
                        "url": None,
                        "updated_at": NOW,
                    }
                ),
                expected_state="ready",
            )
        gateway.call(make_cold)
        assert resolver.resolve() is not None
        clock[0] = 31.0
        assert resolver.resolve() is None
    finally:
        gateway.close()


def test_tuned_probe_bypasses_canary_and_records_tuned_activity(tmp_path: Path) -> None:
    gateway = RepositoryGateway(DatabaseSettings.sqlite(tmp_path / "probe.sqlite3"))
    key = Fernet.generate_key().decode()
    environ = {"VF_CRED_KEY": key}
    observed: dict[str, object] = {}
    try:
        async def seed(repositories):
            service = CredentialService(repositories.credentials, CredentialCipher(key))
            credential_id = await service.put(
                user_id="sv-session",
                provider="vllm-endpoint",
                value="endpoint-secret",
            )
            await repositories.serving_endpoints.put(
                ServingEndpointRecord(
                    model_id="vf-demo",
                    session_id="sv-session",
                    state="ready",
                    url="https://model.trycloudflare.com/v1",
                    api_key_ref=credential_id,
                    updated_at=NOW,
                )
            )
        gateway.call(seed)
        resolver = RegistryTunedResolver(lambda: gateway, environ=environ)

        def forward(request, *, base_url, api_key):
            observed.update(
                request=request,
                base_url=base_url,
                api_key=api_key,
            )
            return ForwardedResponse(
                200,
                {
                    "id": "chatcmpl-probe",
                    "model": request["model"],
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "SELECT 1;",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 3},
                },
            )

        system_prompt = SYSTEM_PROMPTS_BY_CLUSTER["data-pull-sql"]
        response = forward_tuned_completion(
            {
                "model": "client-placeholder",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "Return one row"},
                ],
            },
            gateway=gateway,
            settings=ProxySettings(db_path=tmp_path / "unused.sqlite3"),
            resolver=resolver,
            real_forwarder=forward,
            guardian_draw=lambda: 1.0,
        )

        assert response.status_code == 200
        assert observed["base_url"] == "https://model.trycloudflare.com/v1"
        assert observed["api_key"] == "endpoint-secret"
        assert observed["request"]["model"] == "vf-demo"  # type: ignore[index]
        records = gateway.call(
            lambda repositories: repositories.traffic.list_for_prompt_hash(
                system_prompt_hash(system_prompt)
            )
        )
        assert len(records) == 1
        assert records[0].route_taken == "tuned"
        assert gateway.call(
            lambda repositories: repositories.traffic.latest_route_at("tuned")
        ) is not None
    finally:
        gateway.close()
