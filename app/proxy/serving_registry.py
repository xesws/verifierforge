"""Thirty-second tuned endpoint descriptor cache with per-request key decrypt."""

from __future__ import annotations

from dataclasses import dataclass
import os
import threading
import time
from typing import Callable, Mapping

from app.db import CredentialCipher, RepositoryGateway


GatewayProvider = Callable[[], RepositoryGateway | None]


@dataclass(frozen=True)
class TunedEndpointDescriptor:
    model_id: str
    url: str
    api_key_ref: str


@dataclass(frozen=True, repr=False)
class ResolvedTunedEndpoint:
    model_id: str
    url: str
    api_key: str


class RegistryTunedResolver:
    def __init__(
        self,
        gateway_provider: GatewayProvider,
        *,
        model_id: str = "vf-demo",
        ttl_seconds: float = 30.0,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("serving descriptor cache TTL must be positive")
        self.gateway_provider = gateway_provider
        self.model_id = model_id
        self.ttl_seconds = ttl_seconds
        self.environ = os.environ if environ is None else environ
        self.clock = clock
        self._descriptor: TunedEndpointDescriptor | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def resolve(self) -> ResolvedTunedEndpoint | None:
        descriptor = self._get_descriptor()
        if descriptor is None:
            return None
        gateway = self.gateway_provider()
        if gateway is None:
            return None
        credential = gateway.call(
            lambda repositories: repositories.credentials.get(descriptor.api_key_ref)
        )
        if credential is None:
            return None
        api_key = CredentialCipher.from_env(self.environ).decrypt(
            credential.encrypted_key,
            expected_user_id=credential.user_id,
            expected_provider=credential.provider,
        )
        return ResolvedTunedEndpoint(
            model_id=descriptor.model_id,
            url=descriptor.url,
            api_key=api_key,
        )

    def _get_descriptor(self) -> TunedEndpointDescriptor | None:
        now = self.clock()
        with self._lock:
            if now < self._expires_at:
                return self._descriptor
            gateway = self.gateway_provider()
            descriptor = None
            if gateway is not None:
                record = gateway.call(
                    lambda repositories: repositories.serving_endpoints.get(self.model_id)
                )
                if (
                    record is not None
                    and record.state == "ready"
                    and record.url is not None
                    and record.api_key_ref is not None
                ):
                    descriptor = TunedEndpointDescriptor(
                        model_id=record.model_id,
                        url=record.url,
                        api_key_ref=record.api_key_ref,
                    )
            self._descriptor = descriptor
            self._expires_at = now + self.ttl_seconds
            return descriptor


__all__ = [
    "RegistryTunedResolver",
    "ResolvedTunedEndpoint",
    "TunedEndpointDescriptor",
]
