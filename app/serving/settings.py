"""Secret-safe environment configuration for scale-to-zero serving."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
import os


OFFICIAL_VLLM_IMAGE = (
    "vllm/vllm-openai:v0.10.2@"
    "sha256:df2607b26bdda2875de4832f4d08da0055b4b6e3570347f3a849bcc652771dd6"
)
FALLBACK_VLLM_IMAGE = "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404"
MODEL_JOB_ID = "d4-m3-1p5b-r1-v0125"
MODEL_ARTIFACT_NAME = "serving/step_350"
MODEL_TREE_SHA256 = "7bde853af7c82405fd1356de9bad9b6c421de45a45ce747f63ea2f8a27eda658"
CLOUDFLARED_VERSION = "2026.7.2"
CLOUDFLARED_SHA256 = "ec905ea7b7e327ff8abdde8cb64697a2152de74dbcdbf6aec9db8364eb3886cd"


def _bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ServingSettings:
    enabled: bool = False
    binding: str = "mock"
    model_id: str = "vf-demo"
    budget_usd_cap: float = 5.0
    max_runtime_min: int = 120
    idle_timeout_min: int = 30
    poll_seconds: float = 15.0
    image: str = OFFICIAL_VLLM_IMAGE
    model_job_id: str = MODEL_JOB_ID
    model_artifact_name: str = MODEL_ARTIFACT_NAME
    expected_tree_sha256: str = MODEL_TREE_SHA256
    presign_ttl_seconds: int = 3 * 60 * 60
    install_vllm: bool = False

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "ServingSettings":
        values = os.environ if environ is None else environ
        settings = cls(
            enabled=_bool(values.get("VF_SERVING_WAKE_ENABLED")),
            binding=values.get("VF_SERVING_BINDING", "mock").strip().lower() or "mock",
            model_id=values.get("VF_SERVING_MODEL_ID", "vf-demo").strip() or "vf-demo",
            budget_usd_cap=float(values.get("VF_SERVING_BUDGET_USD_CAP", "5")),
            max_runtime_min=int(values.get("VF_SERVING_MAX_RUNTIME_MIN", "120")),
            idle_timeout_min=int(values.get("VF_SERVING_IDLE_TIMEOUT_MIN", "30")),
            poll_seconds=float(values.get("VF_SERVING_POLL_SECONDS", "15")),
            image=values.get("VF_SERVING_IMAGE", OFFICIAL_VLLM_IMAGE).strip()
            or OFFICIAL_VLLM_IMAGE,
            presign_ttl_seconds=int(
                values.get("VF_SERVING_PRESIGN_TTL_SECONDS", str(3 * 60 * 60))
            ),
            install_vllm=_bool(values.get("VF_SERVING_INSTALL_VLLM")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.binding not in {"mock", "runpod"}:
            raise ValueError("VF_SERVING_BINDING must be mock or runpod")
        if not 0 < self.budget_usd_cap <= 5:
            raise ValueError("VF_SERVING_BUDGET_USD_CAP must be in (0, 5]")
        if not 1 <= self.max_runtime_min <= 120:
            raise ValueError("VF_SERVING_MAX_RUNTIME_MIN must be in [1, 120]")
        if self.idle_timeout_min < 1:
            raise ValueError("VF_SERVING_IDLE_TIMEOUT_MIN must be positive")
        if self.poll_seconds <= 0:
            raise ValueError("VF_SERVING_POLL_SECONDS must be positive")
        if self.presign_ttl_seconds < 600:
            raise ValueError("VF_SERVING_PRESIGN_TTL_SECONDS must be at least 600")
        if "blackwell" in self.image.lower():
            raise ValueError("Blackwell serving images are blocked")
