"""Run D2's real verl GRPO smoke path while preserving VerifierForge contracts."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from core.storage.local import LocalStorage
from trainer.checkpoint_bridge import CheckpointBridge, latest_storage_resume_path
from trainer.grpo_config import GrpoSmokeConfig
from trainer.grpo_dataset import prepare_v1_inputs
from trainer.metric_bridge import VerlMetricBridge
from trainer.plot_metrics import render_curve


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def build_verl_command(
    *,
    config: GrpoSmokeConfig,
    job_id: str,
    train_file: Path,
    validation_file: Path,
    staging_dir: Path,
    resume_path: Path | None,
    python: str | None = None,
) -> list[str]:
    """Return, but do not run, the pinned verl 0.8 synchronous PPO command."""
    reward_file = REPOSITORY_ROOT / "trainer" / "grpo_reward.py"
    overrides = config.verl_overrides(
        train_file=train_file,
        validation_file=validation_file,
        staging_dir=staging_dir,
        reward_file=reward_file,
        job_id=job_id,
        resume_path=resume_path,
    )
    return [python or sys.executable, "-m", "verl.trainer.main_ppo", *overrides]


def run(
    job_id: str,
    config: GrpoSmokeConfig,
    *,
    storage: LocalStorage | None = None,
    poll_interval: float = 1.0,
) -> int:
    """Execute verl and bridge its persistent outputs through ``LocalStorage``.

    No torch, vLLM, or verl import happens in this module.  That keeps command
    construction and bridge behavior testable on the laptop; the child process
    is the only GPU-dependent boundary.
    """
    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")

    storage = storage or LocalStorage()
    runs_root = storage.root.resolve()
    run_dir = runs_root / job_id
    staging_dir = run_dir / ".verl-staging"
    logger_path = staging_dir / "verl-metrics.jsonl"
    staging_dir.mkdir(parents=True, exist_ok=True)

    resume_path = latest_storage_resume_path(storage, job_id)
    if resume_path is None and any(staging_dir.glob("global_step_*")):
        raise RuntimeError(
            f"{job_id} has native staging checkpoints but none published through Storage; "
            "use a new job id or inspect the interrupted run before starting over"
        )
    if resume_path is not None:
        print(f"Resuming {job_id} from Storage checkpoint {resume_path}", flush=True)
    else:
        print(f"Starting {job_id} from scratch", flush=True)

    inputs = prepare_v1_inputs(runs_root, job_id)
    # FileLogger opens this path in write mode on every native invocation.  Clear
    # stale lines before the child starts; append-only public metrics are kept in
    # LocalStorage and de-duplicated by the metric bridge.
    logger_path.unlink(missing_ok=True)
    metric_bridge = VerlMetricBridge(storage, job_id, logger_path)
    checkpoint_bridge = CheckpointBridge(storage, job_id, staging_dir)

    command = build_verl_command(
        config=config,
        job_id=job_id,
        train_file=inputs.train,
        validation_file=inputs.validation,
        staging_dir=staging_dir,
        resume_path=resume_path,
    )
    print("Launching:", " ".join(command), flush=True)
    environment = os.environ.copy()
    environment["VERL_FILE_LOGGER_PATH"] = str(logger_path)
    environment.setdefault("HF_HOME", "/workspace/hf-cache")
    existing_python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{REPOSITORY_ROOT}{os.pathsep}{existing_python_path}"
        if existing_python_path
        else str(REPOSITORY_ROOT)
    )

    process = subprocess.Popen(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        start_new_session=True,
    )
    interrupted = False
    termination_deadline: float | None = None

    def forward_signal(signum: int, _frame: object) -> None:
        del signum
        nonlocal interrupted, termination_deadline
        interrupted = True
        if termination_deadline is None:
            termination_deadline = time.monotonic() + 20
        if process.poll() is None:
            try:
                # The child owns a separate process group so tmux closing does
                # not strand a Ray/vLLM descendant after ``vf kill``.
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    previous_handlers = {
        signum: signal.signal(signum, forward_signal)
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    }
    try:
        while process.poll() is None:
            _drain_bridges(metric_bridge, checkpoint_bridge)
            if termination_deadline is not None and time.monotonic() >= termination_deadline:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                termination_deadline = None
            time.sleep(poll_interval)
        return_code = process.wait()
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        _drain_bridges(metric_bridge, checkpoint_bridge)

    if interrupted:
        print(f"{job_id} interrupted; published Storage checkpoints remain resumable", flush=True)
        return return_code if return_code else 130
    if return_code:
        print(f"verl exited with status {return_code}; no final manifest was published", flush=True)
        return return_code

    final_checkpoint = latest_storage_resume_path(storage, job_id)
    if final_checkpoint is None or _native_step(final_checkpoint) < config.total_steps:
        raise RuntimeError(
            f"verl completed but Storage has no final checkpoint through step {config.total_steps}; "
            "refusing to publish a final artifact"
        )

    _publish_final_artifacts(storage, job_id, config, final_checkpoint)
    print(f"Finished {job_id}; final Storage checkpoint is {final_checkpoint}", flush=True)
    return 0


def _drain_bridges(metric_bridge: VerlMetricBridge, checkpoint_bridge: CheckpointBridge) -> None:
    for metric in metric_bridge.drain():
        print(
            f"metric step={metric.step:04d} reward={metric.reward_mean:.3f} "
            f"pass_at_1={metric.pass_at_1:.3f} entropy={metric.entropy:.3f}",
            flush=True,
        )
    for step in checkpoint_bridge.publish_available():
        print(f"Published Storage checkpoint step_{step}", flush=True)


def _native_step(path: Path) -> int:
    prefix = "global_step_"
    if not path.name.startswith(prefix):
        raise ValueError(f"not a native verl checkpoint: {path}")
    return int(path.name.removeprefix(prefix))


def _publish_final_artifacts(
    storage: LocalStorage,
    job_id: str,
    config: GrpoSmokeConfig,
    final_checkpoint: Path,
) -> None:
    run_dir = storage.root / job_id
    manifest = run_dir / ".final-model.txt"
    manifest.write_text(
        "\n".join(
            (
                f"job_id: {job_id}",
                f"model: {config.model_path}",
                f"completed_step: {_native_step(final_checkpoint)}",
                f"storage_checkpoint: {final_checkpoint}",
                "checkpoint_contents: model, optimizer, extra",
                f"generated_at: {datetime.now(timezone.utc).isoformat()}",
                f"config: {asdict(config)}",
                "weights remain in ckpt/ and are intentionally not synced by vf watch.",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        storage.put_artifact(job_id, "final/model.txt", manifest)
    finally:
        manifest.unlink(missing_ok=True)

    curve = run_dir / ".curve.png"
    try:
        render_curve(run_dir / "metrics.jsonl", curve)
        storage.put_artifact(job_id, "curve.png", curve)
    finally:
        curve.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run VerifierForge's D2 real GRPO smoke")
    parser.add_argument("--job", required=True, help="job identifier")
    parser.add_argument("--config", default="grpo_v1_0p5b", help="trainer/verl_configs config name")
    parser.add_argument("--steps", type=int, help="override total steps (use 2 for the preflight)")
    parser.add_argument("--l4-fallback", action="store_true", help="apply D2's one documented OOM fallback")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="bridge polling interval in seconds")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = GrpoSmokeConfig.load(args.config)
    if args.steps is not None:
        config = config.with_total_steps(args.steps)
    if args.l4_fallback:
        config = config.with_l4_fallback()
    raise SystemExit(run(args.job, config, poll_interval=args.poll_interval))


if __name__ == "__main__":
    main()
