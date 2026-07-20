from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.provisioning import ProvisioningPolicy
from core.provisioning_contracts import (
    DEFAULT_GPU_CANDIDATES,
    DEFAULT_GPU_MAPPINGS,
    GPUClass,
    ProvisionProvider,
    ProvisionSpec,
)


def _spec_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": "job-1",
        "approval_id": "approval-1",
        "requested_by": "owner-a",
        "provider": "runpod",
        "gpu_class": "small_ada",
        "image": "ghcr.io/verifierforge/trainer:dry-run",
        "container_disk_gb": 40,
        "region_pref": ["us-test-1"],
        "env": {"VF_STORAGE_BACKEND": "s3", "VF_MODE": "dry_run"},
        "ports": [22, 8000],
        "ssh_pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeDryRunKey owner-a",
        "budget_usd_cap": 5.0,
        "max_runtime_min": 30,
    }
    payload.update(overrides)
    return payload


def test_provision_spec_round_trip_and_gpu_mapping_excludes_blackwell() -> None:
    spec = ProvisionSpec.model_validate(_spec_payload())

    assert ProvisionSpec.model_validate_json(spec.model_dump_json()) == spec
    assert spec.gpu_class == GPUClass.SMALL_ADA
    assert DEFAULT_GPU_MAPPINGS is DEFAULT_GPU_CANDIDATES
    assert set(DEFAULT_GPU_CANDIDATES) == {
        ProvisionProvider.RUNPOD,
        ProvisionProvider.NEBIUS,
    }
    mapping_text = repr(DEFAULT_GPU_CANDIDATES).lower()
    assert "blackwell" not in mapping_text
    assert "b200" not in mapping_text
    assert "sm_120" not in mapping_text
    assert DEFAULT_GPU_CANDIDATES[ProvisionProvider.RUNPOD][GPUClass.SMALL_ADA] == (
        "NVIDIA RTX 2000 Ada Generation",
        "NVIDIA RTX 4000 Ada Generation",
        "NVIDIA L4",
        "NVIDIA A40",
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"gpu_class": "b200"},
        {"gpu_class": "blackwell"},
        {"gpu_model": "NVIDIA H100"},
        {"env": {"OPENAI_API_KEY": "not-allowed"}},
        {"env": {"VF_TOKEN": "not-allowed"}},
        {"env": {"lowercase": "not-allowed"}},
        {"ports": [8000, 8000]},
        {"budget_usd_cap": float("nan")},
        {"approval_id": ""},
    ],
)
def test_provision_spec_rejects_secret_and_concrete_provider_shapes(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ProvisionSpec.model_validate(_spec_payload(**overrides))


def test_autoprovision_flag_defaults_off() -> None:
    assert ProvisioningPolicy.from_env({}).autoprovision_enabled is False
    assert ProvisioningPolicy.from_env({"VF_AUTOPROVISION": "true"}).autoprovision_enabled is True
