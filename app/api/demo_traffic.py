"""Authenticated one-click mixed traffic simulation for the reviewer demo."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import math
import os
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.auth import require_invitation
from app.proxy.main import dispatch_product_completion
from scripts import traffic_gen


DEFAULT_MIX = "support-ticket=1,invoice=1,data-pull-sql=1"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class DemoTrafficRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = Field(default=200, ge=1, le=500)
    rate: float = Field(default=5.0, gt=0, le=20)

    @field_validator("rate")
    @classmethod
    def finite_rate(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("rate must be finite")
        return value


class DemoTrafficStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = Field(default=200, ge=1, le=500)
    rate: float = Field(default=5.0, gt=0, le=20)
    sent: int = Field(default=0, ge=0)
    success: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    running: bool = False
    error: str | None = Field(default=None, max_length=1000)


RequestBuilder = Callable[[], Mapping[str, list[dict[str, Any]]]]
Replay = Callable[..., traffic_gen.TrafficStats]
Dispatcher = Callable[[Mapping[str, Any]], JSONResponse]


class DemoTrafficController:
    """Own one process-local traffic task and publish immutable snapshots."""

    def __init__(
        self,
        *,
        request_builder: RequestBuilder = traffic_gen.build_requests,
        replay: Replay = traffic_gen.replay_requests,
        dispatcher: Dispatcher = dispatch_product_completion,
    ) -> None:
        self._request_builder = request_builder
        self._replay = replay
        self._dispatcher = dispatcher
        self._lock = threading.Lock()
        self._status = DemoTrafficStatus()

    def status(self) -> DemoTrafficStatus:
        with self._lock:
            return self._status.model_copy(deep=True)

    def start(self, request: DemoTrafficRequest) -> tuple[DemoTrafficStatus, bool]:
        with self._lock:
            if self._status.running:
                return self._status.model_copy(deep=True), False
            self._status = DemoTrafficStatus(
                total=request.total,
                rate=request.rate,
                running=True,
            )

        worker = threading.Thread(
            target=self._run,
            args=(request,),
            daemon=True,
            name="vf-demo-traffic",
        )
        try:
            worker.start()
        except RuntimeError:
            with self._lock:
                self._status = self._status.model_copy(
                    update={"running": False, "error": "Demo traffic task could not start"}
                )
            raise
        return self.status(), True

    def _run(self, request: DemoTrafficRequest) -> None:
        try:
            requests_by_family = self._request_builder()
            self._replay(
                requests_by_family,
                base_url="in-process://verifierforge-proxy",
                rate=request.rate,
                total=request.total,
                mix=traffic_gen.parse_mix(DEFAULT_MIX),
                sender=self._send,
                on_progress=self._publish,
            )
        except Exception:
            with self._lock:
                self._status = self._status.model_copy(
                    update={"error": "Demo traffic task failed"}
                )
        finally:
            with self._lock:
                self._status = self._status.model_copy(update={"running": False})

    def _send(self, _base_url: str, payload: Mapping[str, Any]) -> bool:
        try:
            response = self._dispatcher(payload)
        except Exception:
            return False
        return 200 <= response.status_code < 300

    def _publish(self, stats: traffic_gen.TrafficStats) -> None:
        with self._lock:
            self._status = self._status.model_copy(
                update={
                    "sent": stats.sent,
                    "success": stats.success,
                    "failed": stats.failed,
                }
            )


router = APIRouter()
_CONTROLLER = DemoTrafficController()


def demo_traffic_enabled() -> bool:
    return os.environ.get("VF_DEMO_TRAFFIC_ENABLED", "").strip().lower() in _TRUE_VALUES


def demo_traffic_controller() -> DemoTrafficController:
    return _CONTROLLER


def _require_enabled() -> None:
    if not demo_traffic_enabled():
        raise HTTPException(
            status_code=404,
            detail="Demo traffic simulation is disabled because VF_DEMO_TRAFFIC_ENABLED=false",
        )


@router.post(
    "/demo/traffic",
    response_model=DemoTrafficStatus,
    status_code=202,
    responses={200: {"model": DemoTrafficStatus}},
)
def start_demo_traffic(
    body: DemoTrafficRequest,
    raw_request: Request,
    response: Response,
) -> DemoTrafficStatus:
    require_invitation(raw_request)
    _require_enabled()
    try:
        status, created = demo_traffic_controller().start(body)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Demo traffic task is unavailable") from None
    response.status_code = 202 if created else 200
    return status


@router.get("/demo/traffic/status", response_model=DemoTrafficStatus)
def get_demo_traffic_status(raw_request: Request) -> DemoTrafficStatus:
    require_invitation(raw_request)
    _require_enabled()
    return demo_traffic_controller().status()


__all__ = [
    "DemoTrafficController",
    "DemoTrafficRequest",
    "DemoTrafficStatus",
    "demo_traffic_controller",
    "demo_traffic_enabled",
    "router",
]
