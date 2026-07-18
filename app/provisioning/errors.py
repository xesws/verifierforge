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


class ProvisionAuditError(ProvisioningError):
    """Raised when durable audit cannot be written."""
