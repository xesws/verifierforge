"""Provisioning exceptions with secret-safe messages."""

from __future__ import annotations


class ProvisioningError(Exception):
    """Base class for P-1 provisioning failures."""


class ProvisionRejected(ProvisioningError):
    """Raised when policy rejects a request before provider mutation."""


class ProvisionLifecycleError(ProvisioningError):
    """Raised when a lifecycle step cannot safely continue."""


class ProvisionCreateTimeout(ProvisionLifecycleError):
    """Raised when a provider does not return a handle in time."""


class ProvisionProviderError(ProvisionLifecycleError):
    """Raised for a bounded, secret-safe provider HTTP failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        provider_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider_body = provider_body


class ProvisionAuditError(ProvisioningError):
    """Raised when durable audit cannot be written."""
