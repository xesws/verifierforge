"""A GPU-free training loop used to exercise the run contract end to end."""

import argparse
import math
import random
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from core.contracts import MetricRecord
from core.storage.local import LocalStorage


def _checkpoint_step(checkpoint: Path | None) -> int:
    if checkpoint is None:
        return 0
    try:
        return int(checkpoint.name.removeprefix("step_"))
    except ValueError:
        return 0


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _metrics_for_step(job_id: str, step: int, total_steps: int) -> tuple[float, float, float]:
    """Return deterministic, noisy metrics that look like a healthy short run."""
    progress = step / max(total_steps, 1)
    noise = random.Random(f"{job_id}:{step}")
    reward = _clamp(0.10 + 0.76 * (1 - math.exp(-3.2 * progress)) + noise.uniform(-0.035, 0.035))
    pass_at_1 = _clamp(0.06 + 0.86 * (1 - math.exp(-3.8 * progress)) + noise.uniform(-0.04, 0.04))
    entropy = max(0.05, 1.30 - 0.92 * progress + noise.uniform(-0.045, 0.045))
    return reward, pass_at_1, entropy


def _save_checkpoint(storage: LocalStorage, job_id: str, step: int) -> None:
    """Save a tiny stand-in checkpoint through the same storage interface."""
    with tempfile.TemporaryDirectory(prefix="vf-checkpoint-") as directory:
        checkpoint_file = Path(directory) / "checkpoint.txt"
        checkpoint_file.write_text(f"fake checkpoint for {job_id} at step {step}\n", encoding="utf-8")
        storage.save_checkpoint(job_id, step, checkpoint_file)


def run(job_id: str, steps: int, interval: float, storage: LocalStorage | None = None) -> None:
    """Run until ``steps`` total steps have been recorded, resuming if possible."""
    if steps < 1:
        raise ValueError("steps must be at least 1")
    if interval < 0:
        raise ValueError("interval must be non-negative")

    storage = storage or LocalStorage()
    completed_step = _checkpoint_step(storage.load_latest_checkpoint(job_id))
    if completed_step:
        print(f"Resuming {job_id} from step {completed_step}")
    else:
        print(f"Starting {job_id}")

    for step in range(completed_step + 1, steps + 1):
        time.sleep(interval)
        reward, pass_at_1, entropy = _metrics_for_step(job_id, step, steps)
        record = MetricRecord(
            job_id=job_id,
            step=step,
            reward_mean=reward,
            pass_at_1=pass_at_1,
            entropy=entropy,
            timestamp=datetime.now(timezone.utc),
        )
        storage.append_metrics(job_id, record.model_dump(mode="json"))
        print(
            f"step={step:04d} reward={reward:.3f} "
            f"pass_at_1={pass_at_1:.3f} entropy={entropy:.3f}",
            flush=True,
        )

        if step % 20 == 0 or step == steps:
            _save_checkpoint(storage, job_id, step)

    artifact_source = storage.root / job_id / ".final-model.txt"
    artifact_source.parent.mkdir(parents=True, exist_ok=True)
    artifact_source.write_text(
        f"fake model artifact for {job_id}; completed through step {max(completed_step, steps)}\n",
        encoding="utf-8",
    )
    try:
        storage.put_artifact(job_id, "final/model.txt", artifact_source)
    finally:
        artifact_source.unlink(missing_ok=True)
    print(f"Finished {job_id}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the VerifierForge fake trainer")
    parser.add_argument("--job", required=True, help="job identifier")
    parser.add_argument("--steps", type=int, default=60, help="target total step count")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds per fake step")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(args.job, args.steps, args.interval)


if __name__ == "__main__":
    main()
