"""Testable local helpers for the approval-driven RunPod P2 executor."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.agent_contracts import P2_BASE_MODEL, ProviderPreference, TrainingConfig


P2_CONFIG_NAME = "grpo_v1_0p5b_p2"
P2_TOTAL_STEPS = 100
P2_CHECKPOINT_STEP = 100
P2_WAVE_BUDGET_USD = 5.0
P2_MAX_RUNTIME_MIN = 180
_METRIC_KEY = re.compile(r"/(\d{12})-[0-9a-f]+\.json$")


@dataclass(frozen=True)
class S3ObjectIdentity:
    key: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class P2S3Snapshot:
    latest_step: int
    metric_count: int
    checkpoint_ready: bool
    final_model_ready: bool
    curve_ready: bool

    @property
    def complete(self) -> bool:
        return (
            self.latest_step >= P2_TOTAL_STEPS
            and self.checkpoint_ready
            and self.final_model_ready
            and self.curve_ready
        )


def validate_p2_config(value: Mapping[str, Any]) -> TrainingConfig:
    """Reject an approved config unless it is exactly the bounded P2 profile."""
    config = TrainingConfig.model_validate(value)
    expected = {
        "base_model": P2_BASE_MODEL,
        "steps": P2_TOTAL_STEPS,
        "k": 8,
        "checkpoint_interval": 50,
        "provider_pref": ProviderPreference.RUNPOD,
    }
    actual = {
        "base_model": config.base_model,
        "steps": config.steps,
        "k": config.k,
        "checkpoint_interval": config.checkpoint_interval,
        "provider_pref": config.provider_pref,
    }
    if actual != expected:
        raise ValueError(f"approval config is not the exact P2 execution profile: {actual!r}")
    if config.budget_usd_cap > P2_WAVE_BUDGET_USD:
        raise ValueError("approval budget exceeds the P2 wave cap")
    return config


class S3RunCollector:
    """Observe and collect one S3-backed run without relying on pod-local state."""

    def __init__(self, client: Any, *, bucket: str, prefix: str, job_id: str) -> None:
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.job_id = job_id
        self.job_prefix = "/".join(
            part for part in (self.prefix, "jobs", self.job_id) if part
        )

    def snapshot(self) -> P2S3Snapshot:
        metric_prefix = f"{self.job_prefix}/metrics.jsonl/"
        metric_keys = [key for key in self.list_keys(metric_prefix) if _METRIC_KEY.search(key)]
        steps = [int(_METRIC_KEY.search(key).group(1)) for key in metric_keys]
        keys = set(self.list_keys(f"{self.job_prefix}/"))
        return P2S3Snapshot(
            latest_step=max(steps, default=0),
            metric_count=len(metric_keys),
            checkpoint_ready=f"{self.job_prefix}/ckpt/step_{P2_CHECKPOINT_STEP}/manifest.json" in keys,
            final_model_ready=f"{self.job_prefix}/artifacts/final/model.txt.manifest.json" in keys,
            curve_ready=f"{self.job_prefix}/artifacts/curve.png.manifest.json" in keys,
        )

    def collect(self, destination: Path) -> dict[str, Any]:
        snapshot = self.snapshot()
        if not snapshot.complete:
            raise RuntimeError(f"P2 S3 run is incomplete: {snapshot!r}")
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        identities: dict[str, S3ObjectIdentity] = {}

        metric_prefix = f"{self.job_prefix}/metrics.jsonl/"
        metric_records: list[tuple[int, dict[str, Any]]] = []
        for key in self.list_keys(metric_prefix):
            match = _METRIC_KEY.search(key)
            if match is None:
                continue
            body, identity = self._read_identity(key)
            identities[key] = identity
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError(f"metric object {key!r} is not an object")
            metric_records.append((int(match.group(1)), value))
        metric_records.sort(key=lambda item: item[0])
        metrics_path = destination / "metrics.jsonl"
        metrics_path.write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for _, record in metric_records),
            encoding="utf-8",
        )

        checkpoint_key = f"{self.job_prefix}/ckpt/step_{P2_CHECKPOINT_STEP}/manifest.json"
        checkpoint_body, checkpoint_identity = self._read_identity(checkpoint_key)
        identities[checkpoint_key] = checkpoint_identity
        checkpoint_manifest = json.loads(checkpoint_body)
        for entry in _manifest_files(checkpoint_manifest, checkpoint_key):
            body, identity = self._read_identity(entry["key"])
            if identity.size_bytes != entry["size_bytes"] or identity.sha256 != entry["sha256"]:
                raise RuntimeError(f"S3 checkpoint identity mismatch for {entry['key']!r}")
            identities[identity.key] = identity

        artifact_files: dict[str, str] = {}
        for name, target in (
            ("final/model.txt", destination / "model.txt"),
            ("curve.png", destination / "curve.png"),
        ):
            manifest_key = f"{self.job_prefix}/artifacts/{name}.manifest.json"
            manifest_body, manifest_identity = self._read_identity(manifest_key)
            identities[manifest_key] = manifest_identity
            manifest = json.loads(manifest_body)
            entries = _manifest_files(manifest, manifest_key)
            if len(entries) != 1:
                raise RuntimeError(f"P2 artifact {name!r} must contain exactly one file")
            body, identity = self._read_identity(entries[0]["key"])
            if identity.size_bytes != entries[0]["size_bytes"] or identity.sha256 != entries[0]["sha256"]:
                raise RuntimeError(f"S3 artifact identity mismatch for {name!r}")
            identities[identity.key] = identity
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            artifact_files[name] = str(target)

        inventory = {
            "job_id": self.job_id,
            "bucket": self.bucket,
            "prefix": self.job_prefix,
            "snapshot": snapshot.__dict__,
            "objects": [identity.__dict__ for identity in sorted(identities.values(), key=lambda item: item.key)],
            "local_artifacts": artifact_files,
        }
        inventory_path = destination / "s3-inventory.json"
        inventory_path.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return inventory

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            response = self.client.list_objects_v2(**kwargs)
            keys.extend(str(entry["Key"]) for entry in response.get("Contents", []))
            if not response.get("IsTruncated"):
                return sorted(keys)
            token = response.get("NextContinuationToken")
            if not token:
                raise RuntimeError("truncated S3 listing omitted its continuation token")

    def _read_identity(self, key: str) -> tuple[bytes, S3ObjectIdentity]:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        body = response["Body"].read()
        return body, S3ObjectIdentity(key=key, size_bytes=len(body), sha256=sha256(body).hexdigest())


def _manifest_files(value: object, key: str) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("files"), list):
        raise ValueError(f"S3 manifest {key!r} has no files list")
    files: list[dict[str, Any]] = []
    for entry in value["files"]:
        if not isinstance(entry, dict):
            raise ValueError(f"S3 manifest {key!r} contains a non-object file")
        if not all(field in entry for field in ("key", "size_bytes", "sha256")):
            raise ValueError(f"S3 manifest {key!r} contains an incomplete identity")
        files.append(entry)
    return files


def tree_sha256(paths: Iterable[Path], *, root: Path) -> str:
    """Return one deterministic digest over relative path, size and file hash."""
    digest = sha256()
    for path in sorted((Path(path) for path in paths), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(sha256(content).digest())
    return digest.hexdigest()


__all__ = [
    "P2_CHECKPOINT_STEP",
    "P2_CONFIG_NAME",
    "P2_MAX_RUNTIME_MIN",
    "P2_TOTAL_STEPS",
    "P2_WAVE_BUDGET_USD",
    "P2S3Snapshot",
    "S3ObjectIdentity",
    "S3RunCollector",
    "tree_sha256",
    "validate_p2_config",
]
