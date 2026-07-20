"""Durable wake, readiness, idle-drain, and restart reconciliation."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
import secrets
import threading
from typing import Callable, Mapping
from uuid import uuid4

from app.db import CredentialCipher, CredentialService, RepositoryGateway
from app.db.records import ServingEndpointRecord, ServingEventRecord
from app.provisioning.product import CredentialResolver
from app.serving.runtime import (
    MockServingRuntime,
    RunPodServingRuntime,
    ServingRuntime,
    ServingRuntimeError,
)
from app.serving.settings import ServingSettings
from core.provisioning_contracts import ProvisionHandle, ProvisionProvider
from core.serving_contracts import ServingState, ServingStatus


ENDPOINT_CREDENTIAL_PROVIDER = "vllm-endpoint"
RuntimeFactory = Callable[[ServingSettings], ServingRuntime]


class ServingControlError(RuntimeError):
    """Stable public control-plane failure without secret or provider bodies."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class ServingCoordinator:
    def __init__(
        self,
        *,
        gateway: RepositoryGateway,
        settings: ServingSettings,
        environ: Mapping[str, str] | None = None,
        runtime_factory: RuntimeFactory | None = None,
    ) -> None:
        self.gateway = gateway
        self.settings = settings
        self.environ = os.environ if environ is None else environ
        self.runtime_factory = runtime_factory or self._runtime
        self._threads: dict[str, threading.Thread] = {}
        self._thread_lock = threading.Lock()

    def request_wake(self, model_id: str) -> tuple[ServingStatus, bool]:
        if not self.settings.enabled:
            raise ServingControlError(
                "Scale-to-zero wake is disabled because VF_SERVING_WAKE_ENABLED=false",
                code="wake_disabled",
            )
        if model_id != self.settings.model_id:
            raise ServingControlError("Unknown serving model", code="unknown_model")
        session_id = f"sv-{uuid4().hex[:24]}"
        now = datetime.now(timezone.utc)
        record, created = self.gateway.call(
            lambda repositories: repositories.serving_endpoints.reserve(
                model_id, session_id, now
            )
        )
        if not created:
            return status_from_record(record), False
        try:
            self._bind_endpoint_key(record)
            self._audit(record, action="wake.requested", actor="reviewer")
        except Exception:
            self._set_cold_after_pre_provider_failure(record)
            raise ServingControlError(
                "Serving wake could not persist its credential boundary",
                code="persistence_unavailable",
            ) from None
        self._spawn(record.model_id, record.session_id or session_id)
        refreshed = self._get(model_id)
        return status_from_record(refreshed), True

    def status(self, model_id: str | None = None) -> ServingStatus:
        selected = model_id or self.settings.model_id
        record = self.gateway.call(
            lambda repositories: repositories.serving_endpoints.get(selected)
        )
        if record is None:
            return ServingStatus(
                model_id=selected,
                state=ServingState.COLD,
                detail="No serving session is active",
            )
        return status_from_record(record)

    def drain(self, model_id: str, *, reason: str, actor: str = "idle-reaper") -> ServingStatus:
        record = self._get(model_id)
        if record.state == ServingState.COLD.value:
            return status_from_record(record)
        if record.state != ServingState.DRAINING.value:
            draining = replace(
                record,
                state=ServingState.DRAINING.value,
                url=None,
                detail=reason,
                updated_at=datetime.now(timezone.utc),
            )
            record = self.gateway.call(
                lambda repositories: repositories.serving_endpoints.put(
                    draining, expected_state=record.state
                )
            )
            self._audit(record, action="drain.requested", actor=actor)
        if not record.external_id:
            return status_from_record(self._cold(record, detail="drained before provider allocation"))
        runtime = self.runtime_factory(self.settings)
        try:
            receipt = asyncio.run(runtime.terminate(_handle(record)))
        except Exception:
            failed = replace(
                record,
                error_code="termination_unproven",
                detail="Provider deletion could not be proven",
                updated_at=datetime.now(timezone.utc),
            )
            saved = self.gateway.call(
                lambda repositories: repositories.serving_endpoints.put(
                    failed, expected_state=ServingState.DRAINING.value
                )
            )
            self._audit(saved, action="drain.failed", actor=actor)
            raise ServingControlError(
                "Provider deletion could not be proven",
                code="termination_unproven",
            ) from None
        cold = self._cold(
            record,
            detail=(
                "Provider deletion confirmed; managed inventory is zero"
                if receipt.vf_auto_prefix_count == 0
                else "Provider deletion confirmed"
            ),
        )
        self._audit(cold, action="drain.completed", actor=actor)
        return status_from_record(cold)

    def reap_once(self, *, now: datetime | None = None) -> list[str]:
        observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        records = self.gateway.call(
            lambda repositories: repositories.serving_endpoints.list_active()
        )
        drained: list[str] = []
        for record in records:
            if record.state != ServingState.READY.value:
                if record.requested_at and observed_at - record.requested_at >= timedelta(
                    minutes=self.settings.max_runtime_min
                ):
                    self.drain(record.model_id, reason="maximum serving runtime reached")
                    drained.append(record.model_id)
                continue
            last_tuned = self.gateway.call(
                lambda repositories: repositories.traffic.latest_route_at("tuned")
            )
            anchor = max(
                value
                for value in (record.ready_at, last_tuned, record.requested_at)
                if value is not None
            )
            if observed_at - anchor >= timedelta(minutes=self.settings.idle_timeout_min):
                self.drain(record.model_id, reason="serving endpoint idle timeout reached")
                drained.append(record.model_id)
        return drained

    def reconcile_startup(self) -> list[str]:
        """Conservatively drain incomplete sessions after a process restart."""
        active = self.gateway.call(
            lambda repositories: repositories.serving_endpoints.list_active()
        )
        reconciled: list[str] = []
        for record in active:
            if record.state in {
                ServingState.PROVISIONING.value,
                ServingState.LOADING.value,
                ServingState.DRAINING.value,
            }:
                self.drain(
                    record.model_id,
                    reason="control-plane restart reconciliation",
                    actor="startup-reconciler",
                )
                reconciled.append(record.model_id)
        return reconciled

    def _spawn(self, model_id: str, session_id: str) -> None:
        thread = threading.Thread(
            target=self._run_session,
            args=(model_id, session_id),
            daemon=True,
            name=f"vf-serving-{session_id}",
        )
        with self._thread_lock:
            self._threads[session_id] = thread
        thread.start()

    def _run_session(self, model_id: str, session_id: str) -> None:
        try:
            asyncio.run(self._run_session_async(model_id, session_id))
        finally:
            with self._thread_lock:
                self._threads.pop(session_id, None)

    async def _run_session_async(self, model_id: str, session_id: str) -> None:
        record = self._get(model_id)
        if record.session_id != session_id or record.state != ServingState.PROVISIONING.value:
            return
        endpoint_key = self._endpoint_key(record)
        runtime = self.runtime_factory(self.settings)

        async def allocated(handle: ProvisionHandle, cost: float) -> None:
            current = self._get(model_id)
            loading = replace(
                current,
                state=ServingState.LOADING.value,
                provider=handle.provider.value,
                external_id=handle.external_id,
                gpu_model=handle.labels.get("gpu_model") or handle.labels.get("gpu_display_name"),
                hourly_price_usd=_optional_float(handle.labels.get("hourly_price_usd")),
                cost_accrued_usd=cost,
                detail="capacity allocated; model identity and vLLM readiness pending",
                updated_at=datetime.now(timezone.utc),
            )
            saved = self.gateway.call(
                lambda repositories: repositories.serving_endpoints.put(
                    loading, expected_state=ServingState.PROVISIONING.value
                )
            )
            self._audit(saved, action="provider.allocated", actor="serving-orchestrator")

        try:
            ready = await runtime.start(
                session_id=session_id,
                model_id=model_id,
                endpoint_api_key=endpoint_key,
                on_allocated=allocated,
            )
            current = self._get(model_id)
            saved = self.gateway.call(
                lambda repositories: repositories.serving_endpoints.put(
                    replace(
                        current,
                        state=ServingState.READY.value,
                        url=ready.url,
                        cost_accrued_usd=ready.cost_accrued_usd,
                        cold_start_seconds=ready.cold_start_seconds,
                        ready_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                        detail="S3 identity, vLLM, completion, and public tunnel gates passed",
                        error_code=None,
                    ),
                    expected_state=ServingState.LOADING.value,
                )
            )
            self._audit(saved, action="serving.ready", actor="serving-orchestrator")
        except Exception as error:
            current = self._get(model_id)
            code = (
                "runtime_failed"
                if isinstance(error, ServingRuntimeError)
                else "orchestrator_failed"
            )
            if current.external_id:
                try:
                    self.drain(
                        model_id,
                        reason=f"wake failed: {code}",
                        actor="serving-orchestrator",
                    )
                    current = self._get(model_id)
                except ServingControlError:
                    return
            elif current.state != ServingState.COLD.value:
                current = self._cold(
                    current,
                    detail="Wake failed before provider allocation",
                    error_code=code,
                )
            self._audit(current, action="wake.failed", actor="serving-orchestrator")

    def _runtime(self, settings: ServingSettings) -> ServingRuntime:
        if settings.binding == "mock":
            return MockServingRuntime()
        resolver = CredentialResolver(
            gateway=self.gateway,
            user_id=self.environ.get("VF_SERVING_PROVIDER_USER_ID", "reviewer"),
            provider=ProvisionProvider.RUNPOD,
            environ=self.environ,
        )
        return RunPodServingRuntime(
            settings=settings,
            credential_resolver=resolver,
            environ=self.environ,
        )

    def _bind_endpoint_key(self, record: ServingEndpointRecord) -> None:
        cipher = CredentialCipher.from_env(self.environ)
        endpoint_key = secrets.token_urlsafe(32)

        async def write(repositories):
            service = CredentialService(repositories.credentials, cipher)
            credential_id = await service.put(
                user_id=record.session_id or "missing-session",
                provider=ENDPOINT_CREDENTIAL_PROVIDER,
                value=endpoint_key,
            )
            current = await repositories.serving_endpoints.get(record.model_id)
            if current is None:
                raise ValueError("serving endpoint disappeared during wake")
            return await repositories.serving_endpoints.put(
                replace(current, api_key_ref=credential_id),
                expected_state=ServingState.PROVISIONING.value,
            )

        self.gateway.call(write)

    def _endpoint_key(self, record: ServingEndpointRecord) -> str:
        if not record.api_key_ref:
            raise ServingControlError("Endpoint credential is unavailable", code="credential_missing")
        saved = self.gateway.call(
            lambda repositories: repositories.credentials.get(record.api_key_ref or "")
        )
        if saved is None:
            raise ServingControlError("Endpoint credential is unavailable", code="credential_missing")
        return CredentialCipher.from_env(self.environ).decrypt(
            saved.encrypted_key,
            expected_user_id=saved.user_id,
            expected_provider=saved.provider,
        )

    def _set_cold_after_pre_provider_failure(self, record: ServingEndpointRecord) -> None:
        try:
            self._cold(
                self._get(record.model_id),
                detail="Wake persistence failed before provider allocation",
                error_code="persistence_unavailable",
            )
        except Exception:
            return

    def _cold(
        self,
        record: ServingEndpointRecord,
        *,
        detail: str,
        error_code: str | None = None,
    ) -> ServingEndpointRecord:
        cold = replace(
            record,
            state=ServingState.COLD.value,
            url=None,
            updated_at=datetime.now(timezone.utc),
            detail=detail,
            error_code=error_code,
        )
        return self.gateway.call(
            lambda repositories: repositories.serving_endpoints.put(
                cold, expected_state=record.state
            )
        )

    def _get(self, model_id: str) -> ServingEndpointRecord:
        record = self.gateway.call(
            lambda repositories: repositories.serving_endpoints.get(model_id)
        )
        if record is None:
            raise ServingControlError("Serving endpoint not found", code="not_found")
        return record

    def _audit(self, record: ServingEndpointRecord, *, action: str, actor: str) -> None:
        if not record.session_id:
            return
        event = ServingEventRecord(
            id=uuid4().hex,
            session_id=record.session_id,
            model_id=record.model_id,
            provider=record.provider or "unallocated",
            action=action,
            state=record.state,
            actor=actor,
            occurred_at=datetime.now(timezone.utc),
            external_id=record.external_id,
            detail_json={
                "gpu_model": record.gpu_model,
                "hourly_price_usd": record.hourly_price_usd,
                "cost_accrued_usd": record.cost_accrued_usd,
                "error_code": record.error_code,
                "detail": record.detail,
            },
        )
        self.gateway.call(lambda repositories: repositories.serving_audit.append(event))


def status_from_record(record: ServingEndpointRecord) -> ServingStatus:
    return ServingStatus(
        session_id=record.session_id,
        model_id=record.model_id,
        state=ServingState(record.state),
        url=record.url,
        detail=record.detail,
        error_code=record.error_code,
        gpu_model=record.gpu_model,
        hourly_price_usd=record.hourly_price_usd,
        cost_accrued_usd=record.cost_accrued_usd,
        cold_start_seconds=record.cold_start_seconds,
        updated_at=record.updated_at,
    )


def _handle(record: ServingEndpointRecord) -> ProvisionHandle:
    if not record.external_id or not record.session_id:
        raise ServingControlError("Serving provider handle is unavailable", code="handle_missing")
    labels = {}
    if record.gpu_model:
        labels["gpu_model"] = record.gpu_model
    if record.hourly_price_usd is not None:
        labels["hourly_price_usd"] = f"{record.hourly_price_usd:.6f}"
    return ProvisionHandle(
        provider=ProvisionProvider(record.provider or "runpod"),
        external_id=record.external_id,
        job_id=f"serve-{record.session_id[:24]}",
        approval_id=record.session_id,
        labels=labels,
        created_at=record.requested_at or datetime.now(timezone.utc),
    )


def _optional_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


__all__ = ["ServingControlError", "ServingCoordinator", "status_from_record"]
