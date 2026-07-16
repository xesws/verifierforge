"""Run D2's real verl GRPO smoke path while preserving VerifierForge contracts."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from core.storage.local import LocalStorage
from trainer.checkpoint_bridge import CheckpointBridge, latest_storage_resume_path
from trainer.grpo_config import GrpoSmokeConfig
from trainer.grpo_dataset import prepare_v1_inputs
from trainer.metric_bridge import NormalizedMetric, VerlMetricBridge
from trainer.plot_metrics import render_curve


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_EVIDENCE_ENVIRONMENT = (
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    "VLLM_LOGGING_LEVEL",
    "RAY_DEDUP_LOGS",
    "PYTHONFAULTHANDLER",
)

ENTROPY_BRAKE_START_STEP = 20
ENTROPY_BRAKE_BASELINE_COUNT = 10
ENTROPY_BRAKE_WINDOW = 10
ENTROPY_BRAKE_FRACTION = 0.25


@dataclass(frozen=True)
class EntropyBrakeDecision:
    """The immutable evidence needed to explain an intentional early stop."""

    trigger_step: int
    baseline_entropies: tuple[float, ...]
    baseline_median: float
    threshold: float
    below_threshold_window: tuple[tuple[int, float], ...]


class EntropyBrake:
    """Observe bridged metrics without changing their append-only publication."""

    def __init__(self) -> None:
        self._baseline: list[float] = []
        self._below_threshold: list[tuple[int, float]] = []

    def observe(self, *, step: int, entropy: float) -> EntropyBrakeDecision | None:
        if len(self._baseline) < ENTROPY_BRAKE_BASELINE_COUNT:
            self._baseline.append(entropy)
            return None
        if step <= ENTROPY_BRAKE_START_STEP:
            return None

        baseline_median = median(self._baseline)
        threshold = baseline_median * ENTROPY_BRAKE_FRACTION
        if entropy < threshold:
            self._below_threshold.append((step, entropy))
        else:
            self._below_threshold.clear()

        if len(self._below_threshold) < ENTROPY_BRAKE_WINDOW:
            return None
        return EntropyBrakeDecision(
            trigger_step=step,
            baseline_entropies=tuple(self._baseline),
            baseline_median=baseline_median,
            threshold=threshold,
            below_threshold_window=tuple(self._below_threshold),
        )


def capture_runtime_evidence(
    run_dir: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Path:
    """Atomically preserve runtime identity before a GPU child starts.

    The output is intentionally command-oriented rather than a dependency lock:
    it records the effective environment, installed Python packages, and driver
    facts for post-run diagnosis without ever reading a secret-bearing `.env`.
    """
    run_dir = Path(run_dir)
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    destination = evidence_dir / "runtime-environment.txt"
    lines = [f"captured_at={datetime.now(timezone.utc).isoformat()}", "[environment]"]
    lines.extend(f"{name}={os.environ.get(name, '<unset>')}" for name in RUNTIME_EVIDENCE_ENVIRONMENT)
    lines.extend(
        (
            "",
            "[pip_freeze]",
            _capture_command((sys.executable, "-m", "pip", "freeze"), runner),
            "",
            "[nvidia_smi]",
            _capture_command(
                (
                    "nvidia-smi",
                    "--query-gpu=name,driver_version,memory.total",
                    "--format=csv,noheader",
                ),
                runner,
            ),
            "",
        )
    )
    temporary = destination.with_suffix(".tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    os.replace(temporary, destination)
    return destination


def _capture_command(
    command: tuple[str, ...], runner: Callable[..., subprocess.CompletedProcess[str]]
) -> str:
    """Return a bounded, printable command transcript even when unavailable."""
    rendered = " ".join(command)
    try:
        completed = runner(command, capture_output=True, text=True, check=False)
    except OSError as error:
        return f"command={rendered}\nerror={type(error).__name__}: {error}"

    output = completed.stdout or ""
    error_output = completed.stderr or ""
    return "\n".join(
        (
            f"command={rendered}",
            f"returncode={completed.returncode}",
            "stdout:",
            output.rstrip(),
            "stderr:",
            error_output.rstrip(),
        )
    )


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
    capture_runtime_evidence(run_dir)

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

    inputs = prepare_v1_inputs(runs_root, job_id, dataset_mode=config.dataset_mode)
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
    # verl 0.8 calls FlashAttention's padding helpers even under SDPA.  The
    # child and its Ray workers opt into our layout-only PyTorch replacement;
    # no fake FlashAttention package is exposed to vLLM.
    environment["VF_VERL_TORCH_PADDING_FALLBACK"] = "1"
    if config.vllm_attention_backend is not None:
        environment["VLLM_ATTENTION_BACKEND"] = config.vllm_attention_backend
    existing_python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{REPOSITORY_ROOT / 'trainer'}{os.pathsep}{REPOSITORY_ROOT}{os.pathsep}{existing_python_path}"
        if existing_python_path
        else f"{REPOSITORY_ROOT / 'trainer'}{os.pathsep}{REPOSITORY_ROOT}"
    )

    process = subprocess.Popen(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        start_new_session=True,
    )
    interrupted = False
    brake_decision: EntropyBrakeDecision | None = None
    entropy_brake = EntropyBrake() if config.entropy_brake else None
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
            metrics = _drain_bridges(metric_bridge, checkpoint_bridge)
            if entropy_brake is not None and brake_decision is None:
                for metric in metrics:
                    decision = entropy_brake.observe(step=metric.step, entropy=metric.entropy)
                    if decision is None:
                        continue
                    brake_decision = decision
                    _publish_entropy_brake(storage, job_id, config, decision)
                    if termination_deadline is None:
                        termination_deadline = time.monotonic() + 20
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    print(
                        f"entropy brake at step={decision.trigger_step:04d}; "
                        "terminating without a final-model artifact",
                        flush=True,
                    )
                    break
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

    if brake_decision is not None:
        print(f"{job_id} early-stopped by entropy brake", flush=True)
        return 75
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


def _drain_bridges(
    metric_bridge: VerlMetricBridge, checkpoint_bridge: CheckpointBridge
) -> list[NormalizedMetric]:
    metrics = metric_bridge.drain()
    for metric in metrics:
        print(
            f"metric step={metric.step:04d} reward={metric.reward_mean:.3f} "
            f"pass_at_1={metric.pass_at_1:.3f} entropy={metric.entropy:.3f}",
            flush=True,
        )
    for step in checkpoint_bridge.publish_available():
        print(f"Published Storage checkpoint step_{step}", flush=True)
    return metrics


def _publish_entropy_brake(
    storage: LocalStorage,
    job_id: str,
    config: GrpoSmokeConfig,
    decision: EntropyBrakeDecision,
) -> Path:
    """Atomically persist the stop reason before terminating the GPU child."""
    run_dir = storage.root / job_id
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = latest_storage_resume_path(storage, job_id)
    checkpoint_step = _native_step(checkpoint) if checkpoint else None
    payload = {
        "job_id": job_id,
        "status": "early_stopped",
        "trigger_step": decision.trigger_step,
        "baseline_entropies": list(decision.baseline_entropies),
        "baseline_median": decision.baseline_median,
        "threshold": decision.threshold,
        "below_threshold_window": [
            {"step": step, "entropy": entropy}
            for step, entropy in decision.below_threshold_window
        ],
        "latest_storage_checkpoint_step": checkpoint_step,
        "config": asdict(config),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    evidence = evidence_dir / "entropy-brake.json"
    _write_json_atomic(evidence, payload)
    _write_text_atomic(run_dir / "early_stopped", "entropy brake\n")
    storage.put_artifact(job_id, "entropy-brake.json", evidence)
    return evidence


def _write_json_atomic(path: Path, payload: object) -> None:
    _write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


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
                f"checkpoint_contents: {config.checkpoint_save_contents}",
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
