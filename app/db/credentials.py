"""Fernet protection for provider credentials stored by DB-1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from typing import Mapping
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from .contracts import CredentialStore
from .records import CredentialRecord


KEY_GENERATION_COMMAND = (
    'python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"'
)


class CredentialConfigurationError(RuntimeError):
    """A stable key configuration failure with no key material."""


class CredentialDecryptionError(RuntimeError):
    """A stable ciphertext/scope failure with no plaintext."""


@dataclass(frozen=True)
class CredentialSecret:
    credential_id: str
    user_id: str
    provider: str
    value: str = field(repr=False)


class CredentialCipher:
    def __init__(self, key: str | bytes) -> None:
        encoded = key.encode("utf-8") if isinstance(key, str) else key
        try:
            self._fernet = Fernet(encoded)
        except (TypeError, ValueError):
            raise CredentialConfigurationError(
                "VF_CRED_KEY is invalid; generate a Fernet key with "
                f"{KEY_GENERATION_COMMAND}"
            ) from None

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "CredentialCipher":
        values = os.environ if environ is None else environ
        key = values.get("VF_CRED_KEY", "").strip()
        if not key:
            raise CredentialConfigurationError(
                "VF_CRED_KEY is required for provider credentials; generate it with "
                f"{KEY_GENERATION_COMMAND}"
            )
        return cls(key)

    def encrypt(self, *, user_id: str, provider: str, value: str) -> bytes:
        _required(user_id, "user_id")
        _required(provider, "provider")
        _required(value, "credential value")
        payload = json.dumps(
            {"provider": provider, "user_id": user_id, "value": value},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self._fernet.encrypt(payload)

    def decrypt(
        self,
        ciphertext: bytes,
        *,
        expected_user_id: str,
        expected_provider: str,
    ) -> str:
        try:
            payload = json.loads(self._fernet.decrypt(ciphertext))
        except (InvalidToken, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise CredentialDecryptionError("provider credential cannot be decrypted") from None
        if not isinstance(payload, dict) or (
            payload.get("user_id") != expected_user_id
            or payload.get("provider") != expected_provider
            or not isinstance(payload.get("value"), str)
        ):
            raise CredentialDecryptionError("provider credential scope does not match")
        return payload["value"]


class CredentialService:
    """Plaintext boundary; the repository sees ciphertext only."""

    def __init__(self, store: CredentialStore, cipher: CredentialCipher) -> None:
        self.store = store
        self.cipher = cipher

    async def put(self, *, user_id: str, provider: str, value: str) -> str:
        record = CredentialRecord(
            id=uuid4().hex,
            user_id=user_id,
            provider=provider,
            encrypted_key=self.cipher.encrypt(
                user_id=user_id, provider=provider, value=value
            ),
            created_at=datetime.now(timezone.utc),
        )
        return (await self.store.put(record)).id

    async def get_for_user_provider(
        self, *, user_id: str, provider: str
    ) -> CredentialSecret | None:
        record = await self.store.get_for_user_provider(user_id, provider)
        if record is None:
            return None
        return CredentialSecret(
            credential_id=record.id,
            user_id=record.user_id,
            provider=record.provider,
            value=self.cipher.decrypt(
                record.encrypted_key,
                expected_user_id=user_id,
                expected_provider=provider,
            ),
        )


def _required(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
