"""Publish a complete native verl checkpoint through manifest-last S3 storage.

This is deliberately an operational recovery tool, not a replacement for the
normal checkpoint publication gate.  It exists for a native checkpoint that is
already complete but was quarantined before the S3 proof could resume.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from core.storage.base import Storage
from core.storage.s3 import S3Storage


class NativeCheckpointRecoveryError(RuntimeError):
    """A source or restored checkpoint is not safe to use for resume."""


def publish_native_checkpoint(
    storage: Storage,
    *,
    job_id: str,
    step: int,
    native_checkpoint: Path,
    evidence_path: Path | None = None,
    prior_log: Path | None = None,
) -> dict[str, Any]:
    """Publish and re-materialize one complete ``global_step_<n>`` payload.

    The storage contract publishes a checkpoint directory as ``step_<n>``.
    A temporary wrapper preserves the trainer's required
    ``step_<n>/global_step_<n>`` resume shape without copying the native tree.
    """
    if step < 1:
        raise ValueError("step must be positive")
    native = Path(native_checkpoint).resolve()
    expected_name = f"global_step_{step}"
    if native.name != expected_name:
        raise NativeCheckpointRecoveryError(
            f"native checkpoint must be named {expected_name!r}, got {native.name!r}"
        )
    _validate_native_checkpoint(native)

    with tempfile.TemporaryDirectory(prefix=".vf-s3-native-recovery-") as temporary_directory:
        wrapper = Path(temporary_directory)
        os.symlink(native, wrapper / expected_name, target_is_directory=True)
        storage.save_checkpoint(job_id, step, wrapper)

    restored_wrapper = storage.load_latest_checkpoint(job_id)
    if restored_wrapper is None or restored_wrapper.name != f"step_{step}":
        actual = None if restored_wrapper is None else restored_wrapper.name
        raise NativeCheckpointRecoveryError(
            f"S3 did not materialize expected step_{step} checkpoint (got {actual!r})"
        )
    restored_native = restored_wrapper / expected_name
    _validate_native_checkpoint(restored_native)

    source_identity = tree_identity(native)
    restored_identity = tree_identity(restored_native)
    if source_identity != restored_identity:
        raise NativeCheckpointRecoveryError("materialized native checkpoint identity differs from source")

    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "published",
        "job_id": job_id,
        "step": step,
        "source": source_identity,
        "restored": restored_identity,
    }
    if prior_log is not None:
        log_path = Path(prior_log)
        if not log_path.is_file():
            raise FileNotFoundError(log_path)
        result["prior_log"] = file_identity(log_path)
        storage.put_artifact(job_id, "evidence/s3-first-attempt-train.log", log_path)

    if evidence_path is not None:
        destination = Path(evidence_path)
        _write_json_atomic(destination, result)
        storage.put_artifact(job_id, f"evidence/s3-native-recovery-step-{step}.json", destination)
    return result


def tree_identity(root: Path) -> dict[str, Any]:
    """Return a deterministic, file-level identity for a native checkpoint."""
    root = Path(root)
    entries: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root).as_posix()
        identity = file_identity(path)
        entry = {"path": relative, **identity}
        entries.append(entry)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(identity["size_bytes"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(identity["sha256"]).encode("ascii"))
        digest.update(b"\n")
    if not entries:
        raise NativeCheckpointRecoveryError(f"native checkpoint has no files: {root}")
    return {"tree_sha256": digest.hexdigest(), "file_count": len(entries), "files": entries}


def file_identity(path: Path) -> dict[str, int | str]:
    """Hash one file without retaining its payload in memory."""
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return {"size_bytes": size, "sha256": digest.hexdigest()}


def _validate_native_checkpoint(path: Path) -> None:
    actor = Path(path) / "actor"
    if not Path(path).is_dir() or not (Path(path) / "data.pt").is_file() or not actor.is_dir():
        raise NativeCheckpointRecoveryError(f"not a complete native checkpoint: {path}")
    if not any(actor.glob("model_world_size_*_rank_*.pt")):
        raise NativeCheckpointRecoveryError(f"native checkpoint has no model state: {path}")
    if not any(actor.glob("optim_world_size_*_rank_*.pt")):
        raise NativeCheckpointRecoveryError(f"native checkpoint has no optimizer state: {path}")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish one native verl checkpoint through S3")
    parser.add_argument("--job", required=True)
    parser.add_argument("--step", required=True, type=int)
    parser.add_argument("--native", required=True, type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--prior-log", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    storage = S3Storage.from_env()
    evidence = args.evidence or storage.root / args.job / "evidence" / f"s3-native-recovery-step-{args.step}.json"
    result = publish_native_checkpoint(
        storage,
        job_id=args.job,
        step=args.step,
        native_checkpoint=args.native,
        evidence_path=evidence,
        prior_log=args.prior_log,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
