from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.db.credentials import (
    CredentialCipher,
    CredentialConfigurationError,
    CredentialDecryptionError,
    CredentialService,
)
from app.db.engine import create_database_runtime
from app.db.migration import migrate_sqlite
from app.db.repositories import create_repositories
from app.db.settings import DatabaseSettings


def test_missing_and_invalid_fernet_key_fail_with_generation_guidance() -> None:
    with pytest.raises(CredentialConfigurationError, match="generate") as missing:
        CredentialCipher.from_env({})
    with pytest.raises(CredentialConfigurationError, match="invalid") as invalid:
        CredentialCipher.from_env({"VF_CRED_KEY": "not-a-fernet-key"})

    assert "not-a-fernet-key" not in str(invalid.value)
    assert "VF_CRED_KEY" in str(missing.value)


@pytest.mark.asyncio
async def test_service_round_trip_persists_ciphertext_only(tmp_path: Path) -> None:
    settings = DatabaseSettings.sqlite(tmp_path / "credentials.sqlite3")
    await migrate_sqlite(settings)
    runtime = create_database_runtime(settings)
    repositories = create_repositories(runtime)
    plaintext = "provider-fixture-sentinel"
    service = CredentialService(
        repositories.credentials,
        CredentialCipher(Fernet.generate_key()),
    )

    credential_id = await service.put(
        user_id="owner-1", provider="runpod", value=plaintext
    )
    stored = await repositories.credentials.get(credential_id)
    recovered = await service.get_for_user_provider(
        user_id="owner-1", provider="runpod"
    )

    assert stored is not None
    assert plaintext.encode() not in stored.encrypted_key
    assert plaintext not in repr(stored)
    assert recovered is not None and recovered.value == plaintext
    assert plaintext not in repr(recovered)
    await runtime.close()


def test_ciphertext_is_bound_to_user_and_provider_scope() -> None:
    cipher = CredentialCipher(Fernet.generate_key())
    ciphertext = cipher.encrypt(user_id="owner-1", provider="runpod", value="fixture")

    with pytest.raises(CredentialDecryptionError, match="scope"):
        cipher.decrypt(
            ciphertext,
            expected_user_id="owner-2",
            expected_provider="runpod",
        )
    with pytest.raises(CredentialDecryptionError, match="decrypt"):
        cipher.decrypt(
            b"invalid-ciphertext",
            expected_user_id="owner-1",
            expected_provider="runpod",
        )
