"""Invite-protected scale-to-zero serving wake and status routes."""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.api.auth import require_invitation
from app.db import DatabaseOperationError, repository_gateway
from app.proxy.main import (
    TunedCompletionUnavailable,
    TunedCompletionUpstreamError,
    forward_tuned_completion,
)
from app.serving.session import ServingControlError, ServingCoordinator
from app.serving.settings import ServingSettings
from core.serving_contracts import (
    ServingSleepRequest,
    ServingStatus,
    ServingWakeRequest,
)


router = APIRouter()
_COORDINATOR: ServingCoordinator | None = None
_COORDINATOR_LOCK = threading.Lock()
_REAPER_STARTED = False


def serving_coordinator() -> ServingCoordinator:
    global _COORDINATOR
    if _COORDINATOR is None:
        with _COORDINATOR_LOCK:
            if _COORDINATOR is None:
                _COORDINATOR = ServingCoordinator(
                    gateway=repository_gateway(),
                    settings=ServingSettings.from_env(),
                )
    return _COORDINATOR


@router.post("/serving/wake", response_model=ServingStatus, status_code=202)
def wake_serving(
    request: ServingWakeRequest,
    raw_request: Request,
    response: Response,
) -> ServingStatus:
    require_invitation(raw_request)
    try:
        status, created = serving_coordinator().request_wake(request.model_id)
        response.status_code = 202 if created else 200
        return status
    except ServingControlError as error:
        status_code = 404 if error.code in {"wake_disabled", "unknown_model"} else 409
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    except (DatabaseOperationError, OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Serving control plane is unavailable") from None


@router.get("/serving/status", response_model=ServingStatus)
def serving_status(raw_request: Request, model_id: str | None = None) -> ServingStatus:
    require_invitation(raw_request)
    try:
        return serving_coordinator().status(model_id)
    except (DatabaseOperationError, OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Serving status is unavailable") from None


@router.post("/serving/sleep", response_model=ServingStatus)
def sleep_serving(
    request: ServingSleepRequest,
    raw_request: Request,
) -> ServingStatus:
    """Drain the reviewer endpoint before its browser session is cleared."""

    require_invitation(raw_request)
    try:
        return serving_coordinator().request_sleep(request.model_id)
    except ServingControlError as error:
        status_code = 404 if error.code == "unknown_model" else 409
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    except (DatabaseOperationError, OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Serving shutdown is unavailable") from None


@router.post(
    "/serving/tuned-completion",
    response_model=dict[str, Any],
)
def tuned_completion(
    request: dict[str, Any],
    raw_request: Request,
) -> JSONResponse:
    """Run an authenticated reviewer probe against only the tuned endpoint."""

    require_invitation(raw_request)
    try:
        forwarded = forward_tuned_completion(request)
    except TunedCompletionUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except TunedCompletionUpstreamError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except HTTPException:
        raise
    except (DatabaseOperationError, OSError, RuntimeError, ValueError):
        raise HTTPException(
            status_code=503,
            detail="Tuned completion control plane is unavailable",
        ) from None
    return JSONResponse(
        content=forwarded.payload,
        status_code=forwarded.status_code,
        headers={"X-VerifierForge-Route": "tuned"},
    )


def start_serving_reaper() -> None:
    global _REAPER_STARTED
    try:
        settings = ServingSettings.from_env()
    except ValueError:
        return
    if not settings.enabled or _REAPER_STARTED:
        return
    _REAPER_STARTED = True
    coordinator = serving_coordinator()

    def loop() -> None:
        try:
            coordinator.reconcile_startup()
        except Exception:
            pass
        while True:
            time.sleep(max(30.0, settings.poll_seconds))
            try:
                coordinator.reap_once()
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True, name="vf-serving-idle-reaper").start()


def reset_serving_state_for_tests() -> None:
    global _COORDINATOR, _REAPER_STARTED
    _COORDINATOR = None
    _REAPER_STARTED = False


__all__ = [
    "reset_serving_state_for_tests",
    "router",
    "serving_coordinator",
    "start_serving_reaper",
]
