"""Filesystem-backed run storage for local development."""

import json
import os
import re
import shutil
import uuid
from pathlib import Path

from .base import Storage


_STEP_DIR = re.compile(r"step_(\d+)")


class LocalStorage(Storage):
    """Store each job beneath a simple, inspectable ``runs/`` directory."""

    def __init__(self, root: Path | str | None = None) -> None:
        configured_root = root if root is not None else os.environ.get("VF_RUNS_DIR", "./runs")
        self.root = Path(configured_root)

    def save_checkpoint(self, job_id: str, step: int, path: Path) -> None:
        """Copy a checkpoint into ``step_<n>`` and publish it by rename."""
        if step < 0:
            raise ValueError("checkpoint step must be non-negative")

        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(source)

        checkpoint_root = self._job_dir(job_id) / "ckpt"
        checkpoint_root.mkdir(parents=True, exist_ok=True)
        target = checkpoint_root / f"step_{step}"
        temporary = checkpoint_root / f".step_{step}.{uuid.uuid4().hex}.tmp"

        try:
            if source.is_dir():
                shutil.copytree(source, temporary)
            else:
                temporary.mkdir()
                shutil.copy2(source, temporary / source.name)

            if target.exists():
                # A directory cannot be replaced in one rename when it is non-empty.
                # Move the old complete checkpoint aside first, then atomically publish
                # the fully copied replacement. This keeps both versions intact until
                # the new one is ready.
                previous = checkpoint_root / f".step_{step}.{uuid.uuid4().hex}.old"
                os.rename(target, previous)
                try:
                    os.rename(temporary, target)
                except Exception:
                    os.rename(previous, target)
                    raise
                shutil.rmtree(previous)
            else:
                os.rename(temporary, target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def load_latest_checkpoint(self, job_id: str) -> Path | None:
        """Return the highest numbered complete checkpoint directory."""
        checkpoint_root = self.root / job_id / "ckpt"
        if not checkpoint_root.is_dir():
            return None

        checkpoints: list[tuple[int, Path]] = []
        for candidate in checkpoint_root.iterdir():
            match = _STEP_DIR.fullmatch(candidate.name)
            if candidate.is_dir() and match:
                checkpoints.append((int(match.group(1)), candidate))
        return max(checkpoints, default=(None, None), key=lambda item: item[0])[1]

    def append_metrics(self, job_id: str, record: dict) -> None:
        """Append exactly one JSON record without rewriting prior metrics."""
        metrics_path = self._job_dir(job_id) / "metrics.jsonl"
        with metrics_path.open("a", encoding="utf-8") as handle:
            json.dump(record, handle, separators=(",", ":"))
            handle.write("\n")

    def put_artifact(self, job_id: str, name: str, path: Path) -> None:
        """Copy a file or directory into ``artifacts/<name>``."""
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(source)

        destination = self._artifact_path(job_id, name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            if destination.exists():
                if destination.is_dir():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)

    def get_artifact(self, job_id: str, name: str, dest: Path) -> Path:
        """Copy an artifact to a file path (or into an existing directory)."""
        source = self._artifact_path(job_id, name)
        if not source.exists():
            raise FileNotFoundError(source)

        requested_destination = Path(dest)
        destination = requested_destination / source.name if requested_destination.is_dir() else requested_destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            if destination.exists():
                if destination.is_dir():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
        return destination

    def _job_dir(self, job_id: str) -> Path:
        job_dir = self.root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir

    def _artifact_path(self, job_id: str, name: str) -> Path:
        relative_name = Path(name)
        if relative_name.is_absolute() or ".." in relative_name.parts:
            raise ValueError("artifact name must be a relative path")
        return self._job_dir(job_id) / "artifacts" / relative_name
