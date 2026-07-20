from __future__ import annotations

from datetime import timedelta
import time
from pathlib import Path

from cryptography.fernet import Fernet
import pytest

from app.db.gateway import RepositoryGateway
from app.db.settings import DatabaseSettings
from app.serving.runtime import MockServingRuntime, _verified_callback, _verified_files
from app.serving.session import ServingControlError, ServingCoordinator
from app.serving.settings import ServingSettings
from core.serving_contracts import ServingState


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
        {"url": "https://example.trycloudflare.com", "tree_sha256": expected},
        expected,
    ) == "https://example.trycloudflare.com"


def test_serving_settings_keep_paid_wake_off_and_budget_bounded() -> None:
    assert ServingSettings.from_env({}).enabled is False
    with pytest.raises(ValueError, match=r"\(0, 5\]"):
        ServingSettings.from_env({"VF_SERVING_BUDGET_USD_CAP": "5.01"})
