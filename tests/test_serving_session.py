from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import time
from pathlib import Path

from cryptography.fernet import Fernet
import pytest

from app.db.gateway import RepositoryGateway
from app.db.settings import DatabaseSettings
from app.serving.bootstrap import BOOTSTRAP_SOURCE
from app.serving.runtime import (
    MockServingRuntime,
    ServingRuntimeError,
    _verified_callback,
    _verified_files,
)
from app.serving.runtime import _terminate_with_one_retry
from app.provisioning.errors import ProvisionProviderError
from app.serving.session import ServingControlError, ServingCoordinator
from app.serving.settings import ServingSettings
from core.serving_contracts import ServingState
from core.provisioning_contracts import ProvisionHandle, ProvisionProvider


@pytest.fixture
def coordinator(tmp_path: Path):
    gateway = RepositoryGateway(DatabaseSettings.sqlite(tmp_path / "serving.sqlite3"))
    runtime = MockServingRuntime()
    settings = ServingSettings(enabled=True, idle_timeout_min=1)
    service = ServingCoordinator(
        gateway=gateway,
        settings=settings,
        environ={"VF_CRED_KEY": Fernet.generate_key().decode()},
        runtime_factory=lambda _settings: runtime,
    )
    try:
        yield service, runtime
    finally:
        gateway.close()


def _wait_state(service: ServingCoordinator, state: ServingState):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        observed = service.status()
        if observed.state is state:
            return observed
        time.sleep(0.01)
    raise AssertionError(f"serving state never reached {state.value}")


def test_wake_is_idempotent_then_idle_reaper_cleans_and_allows_second_wake(
    coordinator,
) -> None:
    service, runtime = coordinator
    first, created = service.request_wake("vf-demo")
    assert created is True
    assert first.state in {ServingState.PROVISIONING, ServingState.LOADING, ServingState.READY}
    ready = _wait_state(service, ServingState.READY)
    assert ready.url == "https://mock-serving.example.test/v1"
    assert runtime.starts == 1

    same, created = service.request_wake("vf-demo")
    assert created is False
    assert same.session_id == ready.session_id
    assert runtime.starts == 1

    drained = service.reap_once(now=ready.updated_at + timedelta(minutes=2))
    assert drained == ["vf-demo"]
    assert service.status().state is ServingState.COLD
    assert runtime.terminations == 1

    second, created = service.request_wake("vf-demo")
    assert created is True
    _wait_state(service, ServingState.READY)
    assert second.session_id != ready.session_id
    assert runtime.starts == 2


def test_reviewer_sleep_is_idempotent_and_serializes_provider_termination(
    coordinator,
) -> None:
    service, runtime = coordinator
    service.request_wake("vf-demo")
    _wait_state(service, ServingState.READY)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(service.request_sleep, ["vf-demo", "vf-demo"]))

    assert [result.state for result in results] == [
        ServingState.COLD,
        ServingState.COLD,
    ]
    assert runtime.terminations == 1
    assert service.status().state is ServingState.COLD
    again = service.request_sleep("vf-demo")
    assert again.state is ServingState.COLD
    assert runtime.terminations == 1


def test_reviewer_sleep_rejects_unknown_model_without_provider_work(
    coordinator,
) -> None:
    service, runtime = coordinator
    with pytest.raises(ServingControlError) as captured:
        service.request_sleep("unknown-model")
    assert captured.value.code == "unknown_model"
    assert runtime.terminations == 0


def test_reviewer_sleep_refuses_to_fake_delete_a_real_provider_handle(
    coordinator,
) -> None:
    service, runtime = coordinator
    service.request_wake("vf-demo")
    _wait_state(service, ServingState.READY)
    record = service.gateway.call(
        lambda repositories: repositories.serving_endpoints.get("vf-demo")
    )
    assert record is not None
    service.gateway.call(
        lambda repositories: repositories.serving_endpoints.put(
            replace(record, external_id="real-runpod-handle"),
            expected_state=ServingState.READY.value,
        )
    )

    with pytest.raises(ServingControlError) as captured:
        service.request_sleep("vf-demo")

    assert captured.value.code == "binding_mismatch"
    assert runtime.terminations == 0
    assert service.status().state is ServingState.READY


def test_disabled_wake_has_no_provider_or_database_side_effect(tmp_path: Path) -> None:
    gateway = RepositoryGateway(DatabaseSettings.sqlite(tmp_path / "disabled.sqlite3"))
    runtime = MockServingRuntime()
    service = ServingCoordinator(
        gateway=gateway,
        settings=ServingSettings(enabled=False),
        environ={"VF_CRED_KEY": Fernet.generate_key().decode()},
        runtime_factory=lambda _settings: runtime,
    )
    try:
        with pytest.raises(ServingControlError) as captured:
            service.request_wake("vf-demo")
        assert captured.value.code == "wake_disabled"
        assert runtime.starts == 0
        assert service.status().state is ServingState.COLD
    finally:
        gateway.close()


def test_runtime_failure_returns_to_cold_with_visible_failure_detail(tmp_path: Path) -> None:
    class FailingRuntime(MockServingRuntime):
        async def start(
            self,
            *,
            session_id,
            model_id,
            endpoint_api_key,
            on_allocated,
        ):
            del model_id, endpoint_api_key
            handle = ProvisionHandle(
                provider=ProvisionProvider.RUNPOD,
                external_id="mock-failed-pod",
                job_id=f"serve-{session_id[:20]}",
                approval_id=session_id,
                labels={"gpu_model": "Mock Ada", "hourly_price_usd": "0.1"},
            )
            await on_allocated(handle, 0.0)
            raise ServingRuntimeError("sanitized fixture failure")

    gateway = RepositoryGateway(DatabaseSettings.sqlite(tmp_path / "failed.sqlite3"))
    runtime = FailingRuntime()
    service = ServingCoordinator(
        gateway=gateway,
        settings=ServingSettings(enabled=True),
        environ={"VF_CRED_KEY": Fernet.generate_key().decode()},
        runtime_factory=lambda _settings: runtime,
    )
    try:
        service.request_wake("vf-demo")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            observed = service.status()
            if observed.state is ServingState.COLD and observed.error_code:
                break
            time.sleep(0.01)
        else:
            raise AssertionError("failed serving session did not return to visible cold state")
        assert observed.error_code == "runtime_failed"
        assert "wake failed: runtime_failed" in observed.detail
        assert "provider deletion confirmed" in observed.detail
        assert runtime.terminations == 1
    finally:
        gateway.close()


def test_s3_identity_and_callback_validation() -> None:
    files = [
        {"path": "config.json", "sha256": "a" * 64, "size_bytes": 2, "key": "one"},
        {"path": "weights.bin", "sha256": "b" * 64, "size_bytes": 4, "key": "two"},
    ]
    import hashlib

    digest = hashlib.sha256()
    for entry in files:
        digest.update(entry["path"].encode())
        digest.update(b"\0")
        digest.update(entry["sha256"].encode())
        digest.update(b"\n")
    expected = digest.hexdigest()
    assert _verified_files({"files": files}, expected) == files
    assert _verified_callback(
        {
            "phase": "ready",
            "url": "https://example.trycloudflare.com",
            "tree_sha256": expected,
        },
        expected,
    ) == "https://example.trycloudflare.com"
    assert _verified_callback(
        {
            "phase": "vllm_starting",
            "url": "https://example.trycloudflare.com",
            "tree_sha256": expected,
        },
        expected,
    ) is None
    with pytest.raises(ServingRuntimeError, match="return_code=2"):
        _verified_callback(
            {
                "phase": "failed",
                "return_code": 2,
                "diagnostic": "usage: api_server",
                "url": "https://example.trycloudflare.com",
                "tree_sha256": expected,
            },
            expected,
        )


def test_bootstrap_redacts_secrets_and_waits_for_local_vllm_readiness() -> None:
    assert '"phase": "vllm_starting"' in BOOTSTRAP_SOURCE
    assert '"phase": "ready"' in BOOTSTRAP_SOURCE
    assert '"phase": "failed"' in BOOTSTRAP_SOURCE
    assert "redacted_tail(vllm_log)" in BOOTSTRAP_SOURCE
    assert "http://127.0.0.1:8000/v1/models" in BOOTSTRAP_SOURCE
    assert '"vllm==0.10.2"' in BOOTSTRAP_SOURCE
    assert '"transformers==4.57.6"' in BOOTSTRAP_SOURCE
    assert '"tokenizers==0.22.2"' in BOOTSTRAP_SOURCE
    assert '"huggingface-hub==0.36.2"' in BOOTSTRAP_SOURCE


def test_serving_settings_keep_paid_wake_off_and_budget_bounded() -> None:
    assert ServingSettings.from_env({}).enabled is False
    assert ServingSettings.from_env({"VF_SERVING_INSTALL_VLLM": "true"}).install_vllm is True
    with pytest.raises(ValueError, match=r"\(0, 5\]"):
        ServingSettings.from_env({"VF_SERVING_BUDGET_USD_CAP": "5.01"})


async def test_delete_retries_one_transient_failure_but_never_retries_permission() -> None:
    handle = ProvisionHandle(
        provider=ProvisionProvider.RUNPOD,
        external_id="pod-test",
        job_id="serve-test",
        approval_id="sv-test",
    )

    class Adapter:
        def __init__(self, status_code):
            self.status_code = status_code
            self.calls = 0

        async def terminate(self, _handle):
            self.calls += 1
            if self.calls == 1:
                raise ProvisionProviderError("safe fixture", status_code=self.status_code)

    transient = Adapter(500)
    await _terminate_with_one_retry(
        transient, handle, sleeper=lambda _seconds: _completed()
    )
    assert transient.calls == 2

    denied = Adapter(403)
    with pytest.raises(ProvisionProviderError):
        await _terminate_with_one_retry(
            denied, handle, sleeper=lambda _seconds: _completed()
        )
    assert denied.calls == 1


async def _completed() -> None:
    return None
