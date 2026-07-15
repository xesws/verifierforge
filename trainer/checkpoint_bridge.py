"""Publish completed native verl checkpoints through :class:`LocalStorage`."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from core.storage.local import LocalStorage


_STEP_DIRECTORY = re.compile(r"step_(\d+)")


def _step_from_storage_path(path: Path) -> int:
    """Return the integer part of a LocalStorage ``step_<n>`` directory."""
    match = _STEP_DIRECTORY.fullmatch(path.name)
    if match is None:
        raise ValueError(f"not a LocalStorage checkpoint directory: {path}")
    return int(match.group(1))


def latest_storage_resume_path(storage: LocalStorage, job_id: str) -> Path | None:
    """Return the native ``global_step_<n>`` directory from Storage, if any.

    The native trainer writes a checkpoint directory named ``global_step_<n>``.
    ``LocalStorage`` publishes a wrapper directory as ``step_<n>`` so this
    function intentionally accepts only that published shape.  It prevents an
    interrupted run from silently resuming directly from ``.verl-staging``.
    """
    checkpoint = storage.load_latest_checkpoint(job_id)
    if checkpoint is None:
        return None

    step = _step_from_storage_path(checkpoint)
    native_checkpoint = checkpoint / f"global_step_{step}"
    if not native_checkpoint.is_dir():
        raise RuntimeError(
            f"Storage checkpoint {checkpoint} is missing {native_checkpoint.name}; "
            "refusing to bypass Storage with a staging checkpoint"
        )
    return native_checkpoint


class CheckpointBridge:
    """Copy complete native checkpoints into the append-only run namespace.

    verl writes ``latest_checkpointed_iteration.txt`` only after it has finished
    writing model, optimizer, and dataloader state.  That marker is therefore the
    publication boundary observed here; partially written ``global_step_*``
    directories are never copied.
    """

    def __init__(self, storage: LocalStorage, job_id: str, staging_dir: Path) -> None:
        self.storage = storage
        self.job_id = job_id
        self.staging_dir = Path(staging_dir)
        self._state_path = storage.root / job_id / ".checkpoint-bridge.json"
        self._published_steps = self._load_published_steps()

    def publish_available(self) -> list[int]:
        """Publish the latest checkpoint when verl's completion marker advances."""
        step = self._completed_native_step()
        if step is None or step in self._published_steps:
            return []

        native_checkpoint = self.staging_dir / f"global_step_{step}"
        if not native_checkpoint.is_dir():
            # The marker is authoritative only when the referenced directory is
            # visible too.  A following poll will retry instead of publishing a
            # truncated or absent checkpoint.
            return []

        self._publish(step, native_checkpoint)
        self._published_steps.add(step)
        self._save_published_steps()
        return [step]

    def _completed_native_step(self) -> int | None:
        marker = self.staging_dir / "latest_checkpointed_iteration.txt"
        try:
            value = marker.read_text(encoding="utf-8").strip()
            step = int(value)
        except (FileNotFoundError, ValueError):
            return None
        return step if step >= 0 else None

    def _publish(self, step: int, native_checkpoint: Path) -> None:
        """Wrap a native checkpoint so Storage retains its global-step name."""
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".vf-publish-", dir=self.staging_dir) as temporary:
            wrapper = Path(temporary)
            # ``LocalStorage.save_checkpoint`` copies a directory's contents.
            # A symlink wrapper therefore produces
            # ``ckpt/step_<n>/global_step_<n>`` without an intermediate full copy.
            os.symlink(native_checkpoint, wrapper / native_checkpoint.name, target_is_directory=True)
            self.storage.save_checkpoint(self.job_id, step, wrapper)

    def _load_published_steps(self) -> set[int]:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            return {int(step) for step in raw.get("published_steps", [])}
        except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
            return set()

    def _save_published_steps(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"published_steps": sorted(self._published_steps)}, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, self._state_path)
