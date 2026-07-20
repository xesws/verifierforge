"""P-4 Settings and explicitly confirmed Start Forge routes."""

from __future__ import annotations

import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.api.agent import agent_enabled
from app.db import (
    CredentialConfigurationError,
    DatabaseOperationError,
    repository_gateway,
)
from app.provisioning.policy import ProvisioningPolicy
from app.provisioning.product import (
    ForgeExecutionError,
    credential_source,
    execute_forge,
    get_execution,
    prepare_forge,
    put_provider_credential,
    reserve_start,
)
from core.p4_contracts import (
    CredentialSource,
    ForgeExecutionStatus,
    ForgeLifecycle,
    ProviderCredentialRequest,
    ProviderCredentialStatus,
    StartForgeRequest,
)
from core.provisioning_contracts import ProvisionProvider


router = APIRouter()


@router.put(
    "/settings/provider-credentials/{provider}",
    response_model=ProviderCredentialStatus,
)
def put_credential(
    provider: ProvisionProvider,
    request: ProviderCredentialRequest,
) -> ProviderCredentialStatus:
    try:
        credential_id, updated_at = put_provider_credential(
            repository_gateway(),
            user_id=request.user_id,
            provider=provider,
            api_key=request.api_key.get_secret_value(),
        )
        return ProviderCredentialStatus(
            user_id=request.user_id,
            provider=provider,
            configured=True,
            source=CredentialSource.STORED,
            credential_id=credential_id,
            updated_at=updated_at,
        )
    except CredentialConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (DatabaseOperationError, OSError, ValueError):
        raise HTTPException(
            status_code=503,
            detail="Provider credential could not be stored",
        ) from None


@router.get(
    "/settings/provider-credentials/{provider}",
    response_model=ProviderCredentialStatus,
)
def get_credential_status(
    provider: ProvisionProvider,
    user_id: str = Query(min_length=1, max_length=128),
) -> ProviderCredentialStatus:
    try:
        gateway = repository_gateway()
        record = gateway.call(
            lambda repositories: repositories.credentials.get_for_user_provider(
                user_id, provider.value
            )
        )
        source = credential_source(
            gateway,
            user_id=user_id,
            provider=provider,
        )
        return ProviderCredentialStatus(
            user_id=user_id,
            provider=provider,
            configured=source is not CredentialSource.MISSING,
            source=source,
            credential_id=record.id if record is not None else None,
            updated_at=record.created_at if record is not None else None,
        )
    except (DatabaseOperationError, OSError, ValueError):
        raise HTTPException(
            status_code=503,
            detail="Provider credential status is unavailable",
        ) from None


@router.post(
    "/approvals/{approval_id}/start-forge",
    response_model=ForgeExecutionStatus,
)
def start_forge(
    approval_id: str,
    request: StartForgeRequest,
    background_tasks: BackgroundTasks,
) -> ForgeExecutionStatus:
    _require_execution_enabled()
    try:
        gateway = repository_gateway()
        prepared = prepare_forge(
            gateway,
            approval_id=approval_id,
            requested_by=request.requested_by,
            system_budget_cap=_system_budget_cap(),
        )
        if prepared.status.state is not ForgeLifecycle.APPROVED:
            return prepared.status
        reserved = reserve_start(gateway, prepared)
        background_tasks.add_task(execute_forge, gateway, reserved)
        return reserved.status
    except ForgeExecutionError as error:
        status_code = 404 if str(error) == "Approval not found" else 409
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    except (DatabaseOperationError, OSError, ValueError):
        raise HTTPException(
            status_code=503,
            detail="Start Forge persistence is unavailable",
        ) from None


@router.get(
    "/approvals/{approval_id}/forge-execution",
    response_model=ForgeExecutionStatus,
)
def forge_execution(approval_id: str) -> ForgeExecutionStatus:
    _require_agent_enabled()
    try:
        return get_execution(repository_gateway(), approval_id)
    except ForgeExecutionError as error:
        status_code = 404 if str(error) == "Approval not found" else 409
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    except (DatabaseOperationError, OSError, ValueError):
        raise HTTPException(
            status_code=503,
            detail="Forge execution status is unavailable",
        ) from None


def _require_agent_enabled() -> None:
    if not agent_enabled():
        raise HTTPException(status_code=404, detail="Not found")


def _require_execution_enabled() -> None:
    _require_agent_enabled()
    try:
        policy = ProvisioningPolicy.from_env()
    except ValueError:
        raise HTTPException(status_code=503, detail="Provisioning policy is invalid") from None
    if not policy.autoprovision_enabled:
        raise HTTPException(status_code=404, detail="Not found")


def _system_budget_cap() -> float:
    try:
        value = float(os.environ.get("VF_PROVISION_SYSTEM_BUDGET_USD_CAP", "5"))
    except ValueError:
        raise HTTPException(
            status_code=503,
            detail="VF_PROVISION_SYSTEM_BUDGET_USD_CAP must be numeric",
        ) from None
    if value <= 0:
        raise HTTPException(status_code=503, detail="Provision system budget must be positive")
    return value


__all__ = ["router"]
