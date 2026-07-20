from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from app.db.credentials import CredentialCipher, CredentialService
from app.db.gateway import RepositoryGateway
from app.db.records import ServingEndpointRecord
from app.db.settings import DatabaseSettings
from app.proxy.serving_registry import RegistryTunedResolver


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
