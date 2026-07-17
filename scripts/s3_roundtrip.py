"""Run the bounded real-bucket proof for the S3 Storage backend."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from core.storage.s3 import S3Storage


class S3PermissionFailure(RuntimeError):
    """A real bucket rejected the one permitted authenticated attempt."""


def run(*, job_id: str, metric_count: int, evidence_path: Path) -> dict[str, Any]:
    """Verify checkpoint, append-only metrics, and interrupted publication once."""
    if metric_count < 1:
        raise ValueError("metric_count must be positive")

    with tempfile.TemporaryDirectory(prefix="vf-s3-roundtrip-") as temporary_directory:
        temporary = Path(temporary_directory)
        storage = S3Storage.from_env(cache_root=temporary / "cache")
        checkpoint_source = temporary / "checkpoint"
        checkpoint_source.mkdir()
        payload = b"verifierforge durable checkpoint\n"
        (checkpoint_source / "state.bin").write_bytes(payload)
        (checkpoint_source / "optimizer.bin").write_bytes(b"optimizer state\n")

        try:
            storage.save_checkpoint(job_id, 50, checkpoint_source)
            restored = storage.load_latest_checkpoint(job_id)
            if restored is None:
                raise RuntimeError("S3 did not publish the checkpoint manifest")
            restored_payload = (restored / "state.bin").read_bytes()
            source_sha256 = hashlib.sha256(payload).hexdigest()
            restored_sha256 = hashlib.sha256(restored_payload).hexdigest()
            if restored_sha256 != source_sha256:
                raise RuntimeError("S3 checkpoint SHA-256 mismatch")

            for step in range(1, metric_count + 1):
                storage.append_metrics(
                    job_id,
                    {
                        "job_id": job_id,
                        "step": step,
                        "reward_mean": step / metric_count,
                        "pass_at_1": step / metric_count,
                        "entropy": 1 - (step / metric_count),
                    },
                )
            metrics = storage.read_metrics(job_id)
            if [record["step"] for record in metrics] != list(range(1, metric_count + 1)):
                raise RuntimeError("S3 metrics did not preserve append order")

            interrupted_job = f"{job_id}-interrupted"
            interrupted_source = temporary / "interrupted"
            interrupted_source.mkdir()
            (interrupted_source / "first.bin").write_bytes(b"first")
            (interrupted_source / "second.bin").write_bytes(b"second")
            _prove_interrupted_upload_is_invisible(storage, interrupted_job, interrupted_source)

            result = {
                "status": "passed",
                "job_id": job_id,
                "checkpoint_step": 50,
                "checkpoint_sha256": source_sha256,
                "metric_count": len(metrics),
                "interrupted_manifest_visible": bool(storage._published_steps(interrupted_job)),
                "object_count": len(list(storage._list_keys(storage._key(job_id) + "/"))),
            }
        except Exception as error:
            if _is_permission_failure(error):
                result = {
                    "status": "OWNER-ACTION",
                    "job_id": job_id,
                    "error": f"{type(error).__name__}: {error}",
                    "action": "Attach AmazonS3FullAccess (or equivalent bucket read/write/list permissions) to these AWS credentials.",
                    "rerun": _rerun_command(job_id, metric_count, evidence_path),
                }
                _write_json_atomic(evidence_path, result)
                raise S3PermissionFailure(result["error"]) from error
            raise

    _write_json_atomic(evidence_path, result)
    return result


def _prove_interrupted_upload_is_invisible(storage: S3Storage, job_id: str, source: Path) -> None:
    original = storage._put_file
    uploads = 0

    def interrupt_after_first(key: str, path: Path) -> None:
        nonlocal uploads
        uploads += 1
        if uploads == 2:
            raise OSError("simulated interrupted S3 upload")
        original(key, path)

    storage._put_file = interrupt_after_first
    try:
        try:
            storage.save_checkpoint(job_id, 1, source)
        except OSError as error:
            if str(error) != "simulated interrupted S3 upload":
                raise
        else:
            raise RuntimeError("simulated interrupted upload unexpectedly completed")
    finally:
        storage._put_file = original
    if storage._published_steps(job_id):
        raise RuntimeError("interrupted checkpoint became visible without a manifest")


def _is_permission_failure(error: Exception) -> bool:
    response = getattr(error, "response", None)
    code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
    return code in {"AccessDenied", "AccessDeniedException", "Forbidden", "403"}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError as error:  # pragma: no cover - dependency boundary.
        raise RuntimeError("--load-dotenv requires python-dotenv") from error
    load_dotenv()


def _rerun_command(job_id: str, metric_count: int, evidence_path: Path) -> str:
    return (
        "python -m scripts.s3_roundtrip --load-dotenv "
        f"--job {job_id} --metrics {metric_count} --evidence {evidence_path}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify one real S3 Storage round trip")
    parser.add_argument("--job", required=True, help="isolated proof job id")
    parser.add_argument("--metrics", type=int, default=50, help="number of append-only metric rows")
    parser.add_argument("--evidence", type=Path, required=True, help="atomic JSON evidence destination")
    parser.add_argument("--load-dotenv", action="store_true", help="load local ignored .env before connecting")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.load_dotenv:
        _load_dotenv()
    try:
        result = run(job_id=args.job, metric_count=args.metrics, evidence_path=args.evidence)
    except S3PermissionFailure as error:
        print(f"OWNER-ACTION: {error}")
        raise SystemExit(3) from error
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
