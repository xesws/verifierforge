"""Publish completed native verl checkpoints through :class:`LocalStorage`."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from core.storage.local import LocalStorage
from trainer.serving_smoke import validate_checkpoint_for_serving


_NATIVE_DIRECTORY = re.compile(r"global_step_(\d+)")
ServingGate = Callable[..., object]


class CheckpointPublicationError(RuntimeError):
    """A completed native checkpoint could not be atomically published."""

    def __init__(self, *, step: int, native_checkpoint: Path, cause: Exception) -> None:
        self.step = step
        self.native_checkpoint = Path(native_checkpoint)
        self.cause = cause
        self.quarantined_path: Path | None = None
        super().__init__(
            f"checkpoint publication failed at step {step} for {self.native_checkpoint}: "
            f"{type(cause).__name__}: {cause}"
        )


class CheckpointCapacityError(RuntimeError):
    """Projected checkpoint demand exceeds the permitted free-space budget."""


@dataclass(frozen=True)
class CheckpointBudget:
    """The explicit capacity arithmetic captured before a GRPO child starts."""

    checkpoint_count: int
    hf_export_bytes: int
    full_checkpoint_bytes: int
    projected_peak_bytes: int
    free_bytes: int
    allowed_bytes: int
    source: str

    def as_dict(self) -> dict[str, int | str]:
        return asdict(self)


def latest_storage_resume_path(storage: LocalStorage, job_id: str) -> Path | None:
    """Return the native ``global_step_<n>`` directory from Storage, if any.

    The native trainer writes a checkpoint directory named ``global_step_<n>``.
    ``LocalStorage`` publishes a wrapper directory as ``step_<n>`` so this
    function intentionally accepts only that published shape.  It prevents an
    interrupted run from silently resuming directly from ``.verl-staging``.
    """
    for step, checkpoint in reversed(storage.checkpoint_paths(job_id)):
        native_checkpoint = checkpoint / f"global_step_{step}"
        if _is_resumable_native_checkpoint(native_checkpoint):
            return native_checkpoint
    return None


class CheckpointBridge:
    """Copy complete native checkpoints into the append-only run namespace.

    verl writes ``latest_checkpointed_iteration.txt`` only after it has finished
    writing model, optimizer, and dataloader state.  That marker is therefore the
    publication boundary observed here; partially written ``global_step_*``
    directories are never copied.
    """

    def __init__(
        self,
        storage: LocalStorage,
        job_id: str,
        staging_dir: Path,
        *,
        lora_rank: int = 16,
        lora_alpha: int = 32,
        serving_gate: ServingGate = validate_checkpoint_for_serving,
        serving_gate_timing: str = "per_checkpoint",
    ) -> None:
        if lora_rank < 1 or lora_alpha < 1:
            raise ValueError("lora_rank and lora_alpha must be positive")
        if serving_gate_timing not in ("per_checkpoint", "post_training"):
            raise ValueError(
                "serving_gate_timing must be per_checkpoint or post_training"
            )
        self.storage = storage
        self.job_id = job_id
        self.staging_dir = Path(staging_dir)
        self.lora_rank = lora_rank
        self.lora_alpha = lora_alpha
        self._serving_gate = serving_gate
        self.serving_gate_timing = serving_gate_timing
        self._state_path = storage.root / job_id / ".checkpoint-bridge.json"
        self._published_steps, self._candidate_steps = self._load_state()

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

        try:
            if self.serving_gate_timing == "post_training":
                self._publish_candidate(step, native_checkpoint)
            else:
                self._publish(step, native_checkpoint)
        except Exception as error:
            raise CheckpointPublicationError(
                step=step, native_checkpoint=native_checkpoint, cause=error
            ) from error
        if self.serving_gate_timing == "per_checkpoint":
            self._published_steps.add(step)
        self._save_published_steps()
        return [step]

    def has_candidate(self, step: int) -> bool:
        """Return whether this bridge durably recorded ``step`` as a candidate."""
        return step in self._candidate_steps

    def finalize_candidate(self, step: int) -> Path:
        """Service-test one stored candidate and only then publish it.

        This method is called by a separate process after the trainer and Ray
        runtime have exited. Candidate bytes remain immutable evidence; the
        accepted checkpoint is uploaded as a new published generation.
        """
        if self.serving_gate_timing != "post_training":
            raise ValueError("candidate finalization requires post_training timing")
        if step not in self._candidate_steps:
            raise FileNotFoundError(f"checkpoint candidate step_{step} is not recorded")

        self.staging_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".vf-candidate-step-{step}-", dir=self.staging_dir
        ) as temporary:
            wrapper = Path(temporary) / "wrapper"
            self.storage.get_artifact(
                self.job_id,
                self._candidate_artifact_name(step),
                wrapper,
            )
            native_checkpoint = wrapper / f"global_step_{step}"
            if not native_checkpoint.is_dir():
                raise FileNotFoundError(
                    f"checkpoint candidate step_{step} has no native payload"
                )
            try:
                self._publish(step, native_checkpoint, remove_source=False)
            except Exception as error:
                raise CheckpointPublicationError(
                    step=step,
                    native_checkpoint=native_checkpoint,
                    cause=error,
                ) from error

        self._published_steps.add(step)
        self._save_published_steps()

        checkpoint = self.storage.load_latest_checkpoint(self.job_id)
        if checkpoint is None:
            raise RuntimeError(f"checkpoint step_{step} passed but was not published")
        return checkpoint

    def prepare_resume(self) -> list[Path]:
        """Prune old native payloads and quarantine unpublished stopped-run state."""
        resume = latest_storage_resume_path(self.storage, self.job_id)
        if resume is None:
            return []

        resume_step = int(resume.name.removeprefix("global_step_"))
        self.storage.prune_native_checkpoints(self.job_id, retain_step=resume_step)
        published_steps = {step for step, _ in self.storage.checkpoint_paths(self.job_id)}
        quarantined: list[Path] = []

        for candidate in sorted(self.staging_dir.glob("global_step_*")):
            match = _NATIVE_DIRECTORY.fullmatch(candidate.name)
            if match is None or not candidate.is_dir():
                continue
            step = int(match.group(1))
            if step in published_steps:
                shutil.rmtree(candidate)
                continue
            quarantined_path = self.quarantine_failed_native(candidate)
            if quarantined_path is not None:
                quarantined.append(quarantined_path)

        (self.staging_dir / "latest_checkpointed_iteration.txt").unlink(missing_ok=True)
        return quarantined

    def quarantine_failed_native(self, native_checkpoint: Path) -> Path | None:
        """Atomically preserve a failed completed staging checkpoint as evidence.

        A completion marker means verl finished writing this checkpoint.  Moving
        the directory within the run namespace neither copies bytes nor makes it
        a valid resume source.  The caller stops the child immediately after a
        bridge failure, so a later invocation can only resume through Storage.
        """
        native_checkpoint = Path(native_checkpoint)
        if not native_checkpoint.exists():
            return None
        if native_checkpoint.parent != self.staging_dir or not _NATIVE_DIRECTORY.fullmatch(
            native_checkpoint.name
        ):
            raise ValueError(f"refusing to quarantine a non-staging checkpoint: {native_checkpoint}")

        evidence_root = self.storage.root / self.job_id / "evidence" / "failed-staging"
        evidence_root.mkdir(parents=True, exist_ok=True)
        destination = evidence_root / native_checkpoint.name
        if destination.exists():
            raise RuntimeError(f"failed staging evidence already exists: {destination}")
        os.rename(native_checkpoint, destination)
        return destination

    def checkpoint_budget(
        self,
        *,
        total_steps: int,
        checkpoint_every: int,
        model_path: str,
    ) -> CheckpointBudget:
        """Return or reject the peak space budget required by the retention policy."""
        if total_steps < 1 or checkpoint_every < 1:
            raise ValueError("total_steps and checkpoint_every must be positive")

        measured = latest_storage_resume_path(self.storage, self.job_id)
        if measured is not None:
            full_checkpoint_bytes = _tree_bytes(measured)
            hf_export_bytes = _tree_bytes(measured / "actor" / "huggingface")
            source = f"resume:{measured}"
        else:
            hf_export_bytes = _cached_model_bytes(model_path)
            full_checkpoint_bytes = hf_export_bytes * 4
            source = f"model-cache:{model_path}"

        checkpoint_count = (total_steps + checkpoint_every - 1) // checkpoint_every
        projected_peak_bytes = checkpoint_count * (2 * hf_export_bytes) + 3 * full_checkpoint_bytes
        free_bytes = shutil.disk_usage(self.storage.root).free
        allowed_bytes = free_bytes * 80 // 100
        budget = CheckpointBudget(
            checkpoint_count=checkpoint_count,
            hf_export_bytes=hf_export_bytes,
            full_checkpoint_bytes=full_checkpoint_bytes,
            projected_peak_bytes=projected_peak_bytes,
            free_bytes=free_bytes,
            allowed_bytes=allowed_bytes,
            source=source,
        )
        if projected_peak_bytes > allowed_bytes:
            raise CheckpointCapacityError(
                "checkpoint capacity preflight rejected: "
                f"checkpoint_count={checkpoint_count}, "
                f"hf_export_bytes={hf_export_bytes}, "
                f"full_checkpoint_bytes={full_checkpoint_bytes}, "
                f"projected_peak_bytes={projected_peak_bytes}, "
                f"free_bytes={free_bytes}, allowed_bytes={allowed_bytes}"
            )
        return budget

    def _completed_native_step(self) -> int | None:
        marker = self.staging_dir / "latest_checkpointed_iteration.txt"
        try:
            value = marker.read_text(encoding="utf-8").strip()
            step = int(value)
        except (FileNotFoundError, ValueError):
            return None
        return step if step >= 0 else None

    def _publish(
        self,
        step: int,
        native_checkpoint: Path,
        *,
        remove_source: bool = True,
    ) -> None:
        """Serve-smoke a native checkpoint, then atomically publish its wrapper."""
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        evidence = self.storage.root / self.job_id / "evidence" / "serveability" / f"step_{step}.json"
        try:
            self._serving_gate(
                native_checkpoint,
                lora_rank=self.lora_rank,
                lora_alpha=self.lora_alpha,
                evidence_path=evidence,
            )
        finally:
            if evidence.is_file():
                self.storage.put_artifact(
                    self.job_id,
                    f"serveability/step_{step}.json",
                    evidence,
                )
        with tempfile.TemporaryDirectory(prefix=".vf-publish-", dir=self.staging_dir) as temporary:
            wrapper = Path(temporary)
            # ``LocalStorage.save_checkpoint`` copies a directory's contents.
            # A symlink wrapper therefore produces
            # ``ckpt/step_<n>/global_step_<n>`` without an intermediate full copy.
            os.symlink(native_checkpoint, wrapper / native_checkpoint.name, target_is_directory=True)
            self.storage.save_checkpoint(self.job_id, step, wrapper)
        self.storage.prune_native_checkpoints(self.job_id, retain_step=step)
        if remove_source:
            shutil.rmtree(native_checkpoint)

    def _publish_candidate(self, step: int, native_checkpoint: Path) -> None:
        """Upload a completed native checkpoint without making it publishable."""
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".vf-candidate-", dir=self.staging_dir
        ) as temporary:
            wrapper = Path(temporary)
            os.symlink(native_checkpoint, wrapper / native_checkpoint.name, target_is_directory=True)
            self.storage.put_artifact(
                self.job_id,
                self._candidate_artifact_name(step),
                wrapper,
            )
        self._candidate_steps.add(step)
        shutil.rmtree(native_checkpoint)

    @staticmethod
    def _candidate_artifact_name(step: int) -> str:
        return f"candidate-checkpoints/step_{step}"

    def _load_state(self) -> tuple[set[int], set[int]]:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            return (
                {int(step) for step in raw.get("published_steps", [])},
                {int(step) for step in raw.get("candidate_steps", [])},
            )
        except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
            return set(), set()

    def _save_published_steps(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "published_steps": sorted(self._published_steps),
                    "candidate_steps": sorted(self._candidate_steps),
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(temporary, self._state_path)


def _tree_bytes(path: Path) -> int:
    """Return the byte total of regular files below a directory, or zero."""
    if not path.is_dir():
        return 0
    return sum(candidate.stat().st_size for candidate in path.rglob("*") if candidate.is_file())


def _is_resumable_native_checkpoint(path: Path) -> bool:
    """Recognize the verl payload required for a Storage-only resume.

    An HF export alone intentionally stays under an older ``global_step_*``
    directory for held-out evaluation.  It must never be mistaken for model,
    optimizer, and dataloader state.  These filenames are the explicit verl
    0.8 single-rank payload written by this repository's pinned configuration.
    """
    actor = path / "actor"
    return (
        path.is_dir()
        and (path / "data.pt").is_file()
        and actor.is_dir()
        and any(actor.glob("model_world_size_*_rank_*.pt"))
        and any(actor.glob("optim_world_size_*_rank_*.pt"))
    )


def _cached_model_bytes(model_path: str) -> int:
    """Resolve a cached model directory without allowing hub access at preflight."""
    direct = Path(model_path)
    if direct.is_dir():
        size = _tree_bytes(direct)
    else:
        hf_home = Path(os.environ.get("HF_HOME", "/workspace/hf-cache"))
        snapshot_root = hf_home / "hub" / f"models--{model_path.replace('/', '--')}" / "snapshots"
        snapshots = [candidate for candidate in snapshot_root.iterdir() if candidate.is_dir()] if snapshot_root.is_dir() else []
        if not snapshots:
            raise CheckpointCapacityError(
                f"checkpoint capacity preflight cannot find cached model for {model_path!r} under {snapshot_root}"
            )
        size = _tree_bytes(max(snapshots, key=lambda candidate: candidate.stat().st_mtime))
    if size < 1:
        raise CheckpointCapacityError(f"checkpoint capacity preflight found no model bytes for {model_path!r}")
    return size
