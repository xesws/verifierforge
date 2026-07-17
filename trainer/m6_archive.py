"""Build one reproducible M6 archive manifest without copying model weights."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from core.storage.local import LocalStorage
from trainer.export_compat import is_serveable_export

TREE_HASH_ALGORITHM = {
    "id": "sha256-relative-posix-path-nul-file-sha256-lf-v1",
    "file_digest": "SHA-256 of raw file bytes",
    "file_order": "ascending relative POSIX path",
    "entry_encoding": "UTF-8 relative POSIX path + 0x00 + ASCII lowercase file SHA-256 + 0x0a",
    "tree_digest": "SHA-256 of the concatenated entry byte stream",
}


class M6ArchiveError(RuntimeError):
    """A required report, runtime proof, or immutable artifact is missing."""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main-job", required=True)
    parser.add_argument("--control-job", required=True)
    parser.add_argument("--report-artifact", default="heldout/v0.12.7-report.json")
    parser.add_argument("--archive-artifact", default="m6/v0.12.7-archive-manifest.json")
    parser.add_argument("--manifest-version", default="v0.12.7")
    parser.add_argument("--evidence-directory", default="m6-v0127")
    args = parser.parse_args()
    path = create_archive(
        LocalStorage(),
        args.main_job,
        args.control_job,
        report_artifact=args.report_artifact,
        archive_artifact=args.archive_artifact,
        manifest_version=args.manifest_version,
        evidence_directory=args.evidence_directory,
    )
    print(json.dumps({"archive": str(path), "sha256": sha256_file(path)}, sort_keys=True))


def create_archive(
    storage: LocalStorage,
    main_job: str,
    control_job: str,
    *,
    report_artifact: str,
    archive_artifact: str,
    manifest_version: str = "v0.12.7",
    evidence_directory: str = "m6-v0127",
) -> Path:
    """Atomically publish a compact identity manifest for a completed M3/M4 pair."""
    _validate_artifact_path(report_artifact)
    _validate_artifact_path(archive_artifact)
    _validate_component(manifest_version, "manifest version")
    _validate_component(evidence_directory, "evidence directory")
    main_dir = storage.root / main_job
    control_dir = storage.root / control_job
    report_path = main_dir / "artifacts" / report_artifact
    report = _read_completed_report(report_path)
    selected_step = _integer(report.get("selected_checkpoint_step"), "selected_checkpoint_step")
    selected_native = _native_checkpoint(main_dir, selected_step)
    selected_export = selected_native / "actor" / "serveable_huggingface"
    if not is_serveable_export(selected_export):
        raise M6ArchiveError(f"selected checkpoint is not a completed serveable export: {selected_export}")

    final_step, final_native = _latest_native_checkpoint(main_dir)
    checkpoint_exports = _checkpoint_exports(storage.root, main_dir)
    runtime_evidence = {
        "main": _runtime_identity(main_dir / "evidence" / "runtime-environment.txt"),
        "control": _runtime_identity(control_dir / "evidence" / "runtime-environment.txt"),
    }
    manifest = {
        "schema_version": 2,
        "version": manifest_version,
        "status": "completed",
        "main_job": main_job,
        "control_job": control_job,
        "heldout": {
            "report": _file_identity(storage.root, report_path),
            "before": report["before"],
            "after": report["after"],
            "selected_checkpoint_step": selected_step,
            "selection_rule": report["selection_rule"],
        },
        "selected_checkpoint": _tree_identity(storage.root, selected_export),
        "checkpoint_exports": checkpoint_exports,
        "tree_hash_algorithm": TREE_HASH_ALGORITHM,
        "final_training_checkpoint": {
            "step": final_step,
            **_tree_identity(storage.root, final_native),
        },
        "curves": {
            "m3": _file_identity(storage.root, main_dir / "artifacts" / "curve.png"),
            "m4_random_control": _file_identity(storage.root, control_dir / "artifacts" / "curve.png"),
        },
        "final_artifacts": {
            "m3": _file_identity(storage.root, main_dir / "artifacts" / "final" / "model.txt"),
            "m4_random_control": _file_identity(
                storage.root, control_dir / "artifacts" / "final" / "model.txt"
            ),
        },
        "runtime_evidence": runtime_evidence,
        "evidence_sha256": {
            "m3": _evidence_identities(storage.root, main_dir / "evidence"),
            "m4_random_control": _evidence_identities(storage.root, control_dir / "evidence"),
        },
        "weight_policy": "weights remain in Storage ckpt/; this archive records identities only",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    evidence_path = main_dir / "evidence" / evidence_directory / "archive-manifest.json"
    _write_json_atomic(evidence_path, manifest)
    storage.put_artifact(main_job, archive_artifact, evidence_path)
    return evidence_path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_completed_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise M6ArchiveError(f"cannot read held-out report: {path}") from error
    required = ("before", "after", "selected_checkpoint_step", "selection_rule")
    if report.get("status") != "completed" or any(key not in report for key in required):
        raise M6ArchiveError(f"held-out report is not a completed M5 result: {path}")
    if not isinstance(report["before"], dict) or not isinstance(report["after"], dict):
        raise M6ArchiveError(f"held-out report has malformed metric sections: {path}")
    return report


def _native_checkpoint(run_dir: Path, step: int) -> Path:
    native = Path(run_dir) / "ckpt" / f"step_{step}" / f"global_step_{step}"
    if not native.is_dir():
        raise M6ArchiveError(f"checkpoint step {step} is missing from Storage: {native}")
    return native


def _latest_native_checkpoint(run_dir: Path) -> tuple[int, Path]:
    candidates: list[tuple[int, Path]] = []
    for wrapper in (Path(run_dir) / "ckpt").glob("step_*"):
        try:
            step = int(wrapper.name.removeprefix("step_"))
        except ValueError:
            continue
        native = wrapper / f"global_step_{step}"
        if native.is_dir():
            candidates.append((step, native))
    if not candidates:
        raise M6ArchiveError(f"no Storage checkpoints under {run_dir / 'ckpt'}")
    return max(candidates, key=lambda item: item[0])


def _runtime_identity(path: Path) -> dict[str, str]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise M6ArchiveError(f"runtime evidence is missing: {path}") from error
    if "[pip_freeze]" not in content or "[nvidia_smi]" not in content:
        raise M6ArchiveError(f"runtime evidence lacks pip/driver sections: {path}")
    return {"path": str(path), "sha256": sha256_file(path)}


def _file_identity(root: Path, path: Path) -> dict[str, str]:
    if not Path(path).is_file():
        raise M6ArchiveError(f"required artifact is missing: {path}")
    return {"path": _relative(root, path), "sha256": sha256_file(path)}


def _tree_identity(root: Path, path: Path) -> dict[str, Any]:
    if not Path(path).is_dir():
        raise M6ArchiveError(f"required directory is missing: {path}")
    digest = hashlib.sha256()
    entries: list[dict[str, str | int]] = []
    files = sorted(
        (candidate for candidate in Path(path).rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(path).as_posix(),
    )
    for candidate in files:
        relative_path = candidate.relative_to(path).as_posix()
        file_sha256 = sha256_file(candidate)
        entries.append(
            {
                "path": relative_path,
                "size_bytes": candidate.stat().st_size,
                "sha256": file_sha256,
            }
        )
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_sha256.encode("ascii"))
        digest.update(b"\n")
    return {
        "path": _relative(root, path),
        "file_count": len(files),
        "files": entries,
        "tree_sha256": digest.hexdigest(),
    }


def _checkpoint_exports(root: Path, main_dir: Path) -> list[dict[str, object]]:
    exports: list[dict[str, object]] = []
    for wrapper in (Path(main_dir) / "ckpt").glob("step_*"):
        try:
            step = int(wrapper.name.removeprefix("step_"))
        except ValueError:
            continue
        native = wrapper / f"global_step_{step}"
        export = native / "actor" / "serveable_huggingface"
        if not export.is_dir():
            continue
        if not is_serveable_export(export):
            raise M6ArchiveError(f"checkpoint export is not serveable: {export}")
        exports.append({"step": step, **_tree_identity(root, export)})
    if not exports:
        raise M6ArchiveError(f"no serveable checkpoint exports under {main_dir / 'ckpt'}")
    return sorted(exports, key=lambda entry: int(entry["step"]))


def _evidence_identities(root: Path, evidence_dir: Path) -> list[dict[str, str]]:
    if not Path(evidence_dir).is_dir():
        raise M6ArchiveError(f"evidence directory is missing: {evidence_dir}")
    return [_file_identity(root, path) for path in sorted(evidence_dir.rglob("*")) if path.is_file()]


def _relative(root: Path, path: Path) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError as error:
        raise M6ArchiveError(f"artifact escapes Storage root: {path}") from error


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise M6ArchiveError(f"{name} must be a non-negative integer")
    return value


def _validate_artifact_path(value: str) -> None:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError("artifact paths must be non-empty relative Storage paths")


def _validate_component(value: str, name: str) -> None:
    path = Path(value)
    if not value or path.is_absolute() or len(path.parts) != 1 or value in {".", ".."}:
        raise ValueError(f"{name} must be a single non-empty path component")


def _write_json_atomic(path: Path, payload: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


if __name__ == "__main__":
    main()
