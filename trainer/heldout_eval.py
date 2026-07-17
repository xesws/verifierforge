"""Evaluate exported M3 checkpoints on the immutable held-out set, pod-local only."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from core.rewards.nl2sql import NL2SQLVerifier
from core.storage.local import LocalStorage
from trainer.export_compat import is_serveable_export, serveable_export_path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HELDOUT_PATH = REPOSITORY_ROOT / "data" / "nl2sql" / "v0.10.0-heldout.jsonl"
FREEZE_MANIFEST_PATH = REPOSITORY_ROOT / "data" / "nl2sql" / "v0.10.2-u3-freeze-manifest.json"
BEFORE_METRICS = {
    "pass_at_1": 0.5833333333333334,
    "pass_at_8": 0.7666666666666667,
    "mixed_fraction": 0.4666666666666667,
}
SAMPLE_COUNT = 60 * 8


class HeldoutEvaluationError(RuntimeError):
    """An evaluation identity, serving, or evidence failure."""


@dataclass(frozen=True)
class Checkpoint:
    step: int
    native_path: Path
    hf_path: Path


@dataclass(frozen=True)
class CheckpointResult:
    step: int
    checkpoint_sha256: str
    evidence_sha256: str | None
    samples_sha256: str | None
    metrics: dict[str, float] | None
    status: str
    detail: str | None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, help="completed M3 job whose checkpoints are evaluated")
    parser.add_argument("--control-job", required=True, help="completed M4 control job for report linkage")
    parser.add_argument("--port", type=int, default=8011, help="first pod-loopback vLLM port to try")
    parser.add_argument(
        "--evidence-name",
        default="heldout-after",
        help="job-local evidence directory name; use a new value to preserve a prior attempt",
    )
    parser.add_argument(
        "--artifact-name",
        default="heldout/report.json",
        help="Storage artifact name for this evaluation report",
    )
    parser.add_argument(
        "--require-serveable",
        action="store_true",
        help="evaluate only converted actor/serveable_huggingface exports",
    )
    args = parser.parse_args()
    raise SystemExit(
        run(
            args.job,
            args.control_job,
            port=args.port,
            evidence_name=args.evidence_name,
            artifact_name=args.artifact_name,
            require_serveable=args.require_serveable,
        )
    )


def run(
    main_job: str,
    control_job: str,
    *,
    port: int = 8011,
    evidence_name: str = "heldout-after",
    artifact_name: str = "heldout/report.json",
    require_serveable: bool = False,
) -> int:
    """Evaluate every exported checkpoint, then atomically publish one report."""
    _validate_output_names(evidence_name, artifact_name)
    storage = LocalStorage()
    run_dir = storage.root / main_job
    evidence_dir = run_dir / "evidence" / evidence_name
    evidence_dir.mkdir(parents=True, exist_ok=True)
    try:
        frozen_identity = verify_frozen_identity()
        checkpoints = eligible_checkpoints(storage.root, main_job, require_serveable=require_serveable)
        control_curve = control_curve_identity(storage.root, control_job)
    except HeldoutEvaluationError as error:
        _publish_unavailable_report(
            storage, main_job, control_job, evidence_dir, str(error), artifact_name=artifact_name
        )
        return 2

    results: list[CheckpointResult] = []
    for offset, checkpoint in enumerate(checkpoints):
        result = evaluate_checkpoint(
            checkpoint,
            evidence_dir / f"step_{checkpoint.step}",
            port=port + offset,
        )
        results.append(result)
        if result.status != "completed":
            _publish_unavailable_report(
                storage,
                main_job,
                control_job,
                evidence_dir,
                f"checkpoint step {checkpoint.step} is unavailable: {result.detail}",
                checkpoint_results=results,
                frozen_identity=frozen_identity,
                control_curve=control_curve,
                artifact_name=artifact_name,
            )
            return 2

    best = select_best_checkpoint(results)
    report = {
        "schema_version": 1,
        "status": "completed",
        "main_job": main_job,
        "control_job": control_job,
        "frozen_identity": frozen_identity,
        "before": BEFORE_METRICS,
        "after": best.metrics,
        "selected_checkpoint_step": best.step,
        "selection_rule": "maximum held-out pass_at_1; lower checkpoint step breaks ties",
        "control_curve": control_curve,
        "checkpoints": [asdict(result) for result in results],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    report_path = evidence_dir / "report.json"
    _write_json_atomic(report_path, report)
    storage.put_artifact(main_job, artifact_name, report_path)
    print(json.dumps({"selected_checkpoint_step": best.step, **(best.metrics or {})}, sort_keys=True))
    return 0


def eligible_checkpoints(
    runs_root: Path, job_id: str, *, require_serveable: bool = False
) -> list[Checkpoint]:
    """Return all exported Storage checkpoints in numeric step order."""
    checkpoint_root = Path(runs_root) / job_id / "ckpt"
    candidates: list[Checkpoint] = []
    for wrapper in checkpoint_root.glob("step_*"):
        try:
            step = int(wrapper.name.removeprefix("step_"))
        except ValueError:
            continue
        native = wrapper / f"global_step_{step}"
        source_hf_path = native / "actor" / "huggingface"
        hf_path = serveable_export_path(native) if require_serveable else source_hf_path
        if require_serveable and not is_serveable_export(hf_path):
            raise HeldoutEvaluationError(
                f"checkpoint step {step} lacks a completed serveable Hugging Face export"
            )
        if not native.is_dir() or not hf_path.is_dir() or not list(hf_path.glob("*.safetensors")):
            raise HeldoutEvaluationError(
                f"checkpoint step {step} lacks an exported Hugging Face safetensors model"
            )
        candidates.append(Checkpoint(step=step, native_path=native, hf_path=hf_path))
    candidates.sort(key=lambda checkpoint: checkpoint.step)
    if not candidates:
        raise HeldoutEvaluationError(f"{job_id} has no eligible exported Storage checkpoints")
    return candidates


def verify_frozen_identity() -> dict[str, Any]:
    """Check the current held-out file against the immutable three-piece manifest."""
    try:
        manifest = json.loads(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))
        expected = manifest["frozen_identities"]["heldout_evaluation_set"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise HeldoutEvaluationError("cannot read the frozen held-out manifest") from error

    digest = _sha256_file(HELDOUT_PATH)
    if digest != expected.get("sha256"):
        raise HeldoutEvaluationError("held-out SHA-256 differs from the frozen manifest")
    if expected.get("record_count") != 60:
        raise HeldoutEvaluationError("frozen manifest does not declare 60 held-out records")
    return {
        "path": str(HELDOUT_PATH),
        "sha256": digest,
        "record_count": 60,
        "verifier_version": NL2SQLVerifier.VERSION,
        "verifier_source_sha256": _sha256_file(REPOSITORY_ROOT / "core" / "rewards" / "nl2sql.py"),
    }


def control_curve_identity(runs_root: Path, control_job: str) -> dict[str, str]:
    curve = Path(runs_root) / control_job / "artifacts" / "curve.png"
    if not curve.is_file():
        raise HeldoutEvaluationError(f"control curve is missing: {curve}")
    return {"path": str(curve), "sha256": _sha256_file(curve)}


def evaluate_checkpoint(checkpoint: Checkpoint, evidence_dir: Path, *, port: int) -> CheckpointResult:
    """Run one local server/evaluator pair and preserve its full evidence."""
    evidence_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_sha256 = _tree_sha256(checkpoint.hf_path)
    try:
        model_name, process, server_log = start_server(checkpoint, evidence_dir, port=port)
        try:
            wait_for_server(process, port=port, log_path=server_log)
            evidence_path, samples_path = run_gate_a(
                checkpoint,
                evidence_dir,
                port=port,
                model_name=model_name,
            )
        finally:
            stop_server(process)
        metrics = verify_evaluation_evidence(evidence_path, samples_path)
        result = CheckpointResult(
            step=checkpoint.step,
            checkpoint_sha256=checkpoint_sha256,
            evidence_sha256=_sha256_file(evidence_path),
            samples_sha256=_sha256_file(samples_path),
            metrics=metrics,
            status="completed",
            detail=None,
        )
    except Exception as error:
        result = CheckpointResult(
            step=checkpoint.step,
            checkpoint_sha256=checkpoint_sha256,
            evidence_sha256=None,
            samples_sha256=None,
            metrics=None,
            status="unavailable",
            detail=_safe_error(error),
        )
    _write_json_atomic(evidence_dir / "checkpoint-result.json", asdict(result))
    return result


def start_server(checkpoint: Checkpoint, evidence_dir: Path, *, port: int) -> tuple[str, subprocess.Popen[str], Path]:
    """Start one loopback-only vLLM server for a self-contained HF export."""
    executable = Path(sys.executable).with_name("vllm")
    if not executable.is_file():
        raise HeldoutEvaluationError(f"vLLM executable is unavailable: {executable}")
    model_name = f"vf-heldout-step-{checkpoint.step}"
    server_log = evidence_dir / "vllm-server.log"
    environment = os.environ.copy()
    environment.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "VLLM_LOGGING_LEVEL": "INFO",
            "RAY_DEDUP_LOGS": "0",
            "PYTHONFAULTHANDLER": "1",
        }
    )
    command = [
        str(executable),
        "serve",
        str(checkpoint.hf_path),
        "--served-model-name",
        model_name,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--dtype",
        "bfloat16",
        "--gpu-memory-utilization",
        "0.45",
        "--tensor-parallel-size",
        "1",
        "--disable-log-stats",
    ]
    stream = server_log.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        stdout=stream,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    stream.close()
    _write_json_atomic(
        evidence_dir / "server-command.json",
        {"command": command, "model_name": model_name, "port": port},
    )
    return model_name, process, server_log


def wait_for_server(process: subprocess.Popen[str], *, port: int, log_path: Path) -> None:
    deadline = time.monotonic() + 300
    url = f"http://127.0.0.1:{port}/v1/models"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise HeldoutEvaluationError(f"vLLM exited before readiness: {_tail(log_path)}")
        try:
            with urlopen(url, timeout=3) as response:  # noqa: S310 - loopback URL is constructed above.
                if response.status == 200:
                    return
        except URLError:
            pass
        time.sleep(1)
    raise HeldoutEvaluationError(f"vLLM readiness timeout: {_tail(log_path)}")


def run_gate_a(
    checkpoint: Checkpoint,
    evidence_dir: Path,
    *,
    port: int,
    model_name: str,
) -> tuple[Path, Path]:
    """Run the existing OpenAI-compatible evaluator against loopback only."""
    report = evidence_dir / "gate-a-evidence.json"
    samples = evidence_dir / "gate-a-samples.jsonl"
    environment = os.environ.copy()
    environment.update(
        {
            "VF_EVAL_BASE_URL": f"http://127.0.0.1:{port}/v1",
            "VF_EVAL_API_KEY": "vf-local-eval",
            "VF_EVAL_MODEL": model_name,
        }
    )
    command = [
        sys.executable,
        "-m",
        "scripts.gate_a",
        "--input",
        str(HELDOUT_PATH),
        "--k",
        "8",
        "--workers",
        "8",
        "--report",
        str(report),
        "--save-samples",
        "--samples-output",
        str(samples),
        "--reference",
    ]
    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    _write_text_atomic(evidence_dir / "gate-a.stdout.txt", completed.stdout)
    _write_text_atomic(evidence_dir / "gate-a.stderr.txt", completed.stderr)
    if completed.returncode != 0:
        raise HeldoutEvaluationError(f"gate_a returned {completed.returncode}")
    return report, samples


def verify_evaluation_evidence(evidence_path: Path, samples_path: Path) -> dict[str, float]:
    """Reject an incomplete or mismatched evaluator result before selection."""
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HeldoutEvaluationError("cannot read held-out evaluation evidence") from error
    if payload.get("status") != "completed" or payload.get("mode") != "reference":
        raise HeldoutEvaluationError("held-out evidence is not a completed reference evaluation")
    if payload.get("input_sha256") != _sha256_file(HELDOUT_PATH):
        raise HeldoutEvaluationError("held-out evidence input hash mismatch")
    if payload.get("candidate_count") != 60 or payload.get("k") != 8:
        raise HeldoutEvaluationError("held-out evidence has an unexpected record count or k")
    if payload.get("sample_count") != SAMPLE_COUNT:
        raise HeldoutEvaluationError("held-out evidence does not contain 480 samples")
    saved = payload.get("sample_evidence")
    if not isinstance(saved, dict) or saved.get("sample_count") != SAMPLE_COUNT:
        raise HeldoutEvaluationError("held-out evidence lacks complete sample evidence")
    if saved.get("sha256") != _sha256_file(samples_path):
        raise HeldoutEvaluationError("held-out sample evidence hash mismatch")
    verifier = payload.get("verifier")
    if not isinstance(verifier, dict) or verifier.get("version") != NL2SQLVerifier.VERSION:
        raise HeldoutEvaluationError("held-out evidence verifier version mismatch")
    try:
        return {
            "pass_at_1": float(payload["pass_at_1"]),
            "pass_at_8": float(payload["pass_at_8"]),
            "mixed_fraction": float(payload["mixed_fraction"]),
        }
    except (KeyError, TypeError, ValueError) as error:
        raise HeldoutEvaluationError("held-out evidence has malformed metrics") from error


def select_best_checkpoint(results: list[CheckpointResult]) -> CheckpointResult:
    """Select only among fully evaluated checkpoints with the declared tie-break."""
    eligible = [result for result in results if result.status == "completed" and result.metrics is not None]
    if len(eligible) != len(results):
        raise HeldoutEvaluationError("cannot select a best checkpoint with unavailable evaluations")
    return min(eligible, key=lambda result: (-result.metrics["pass_at_1"], result.step))


def stop_server(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=20)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _publish_unavailable_report(
    storage: LocalStorage,
    main_job: str,
    control_job: str,
    evidence_dir: Path,
    detail: str,
    *,
    checkpoint_results: list[CheckpointResult] | None = None,
    frozen_identity: dict[str, Any] | None = None,
    control_curve: dict[str, str] | None = None,
    artifact_name: str = "heldout/report.json",
) -> None:
    report = {
        "schema_version": 1,
        "status": "unavailable",
        "main_job": main_job,
        "control_job": control_job,
        "before": BEFORE_METRICS,
        "after": None,
        "detail": detail,
        "frozen_identity": frozen_identity,
        "control_curve": control_curve,
        "checkpoints": [asdict(result) for result in checkpoint_results or []],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = evidence_dir / "report.json"
    _write_json_atomic(path, report)
    storage.put_artifact(main_job, artifact_name, path)


def _validate_output_names(evidence_name: str, artifact_name: str) -> None:
    if Path(evidence_name).name != evidence_name or not evidence_name:
        raise ValueError("evidence_name must be one job-local directory name")
    artifact = Path(artifact_name)
    if artifact.is_absolute() or ".." in artifact.parts or not artifact_name:
        raise ValueError("artifact_name must be a relative Storage artifact path")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _tail(path: Path, *, limit: int = 2000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return "<log unavailable>"


def _safe_error(error: BaseException) -> str:
    return f"{type(error).__name__}: {str(error)[:1000]}"


def _write_json_atomic(path: Path, payload: object) -> None:
    _write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


if __name__ == "__main__":
    main()
