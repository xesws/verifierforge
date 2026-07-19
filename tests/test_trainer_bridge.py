import json
from dataclasses import replace
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from core.storage.local import LocalStorage
from trainer import checkpoint_bridge as checkpoint_bridge_module
from trainer.checkpoint_bridge import (
    CheckpointBridge,
    CheckpointCapacityError,
    CheckpointPublicationError,
    latest_storage_resume_path,
)
from trainer.grpo_config import GrpoSmokeConfig
from trainer.grpo_dataset import build_verl_rows
from trainer.grpo_train import (
    _preserve_checkpoint_failure,
    _publish_final_artifacts,
    build_verl_command,
    capture_runtime_evidence,
)
from trainer.metric_bridge import VerlMetricBridge
from trainer.plot_metrics import render_curve


def _write_native_checkpoint(root: Path, step: int, *, hf_export: bool = True) -> Path:
    """Create the minimal pinned-verl checkpoint shape used by bridge tests."""
    native = root / f"global_step_{step}"
    actor = native / "actor"
    actor.mkdir(parents=True)
    (actor / "model_world_size_1_rank_0.pt").write_bytes(b"model-state")
    (actor / "optim_world_size_1_rank_0.pt").write_bytes(b"optimizer-state")
    (actor / "extra_state_world_size_1_rank_0.pt").write_bytes(b"extra-state")
    (native / "data.pt").write_bytes(b"dataloader-state")
    if hf_export:
        hf = actor / "huggingface"
        hf.mkdir()
        (hf / "model.safetensors").write_bytes(b"hf-weights")
        (hf / "config.json").write_text("{}", encoding="utf-8")
    return native


def _passing_serving_gate(_native_checkpoint: Path, **kwargs: object) -> None:
    """Keep bridge unit tests GPU-free while recording the required evidence path."""
    evidence = Path(str(kwargs["evidence_path"]))
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text('{"status":"completed"}\n', encoding="utf-8")


def _bridge(storage: LocalStorage, job_id: str, staging: Path) -> CheckpointBridge:
    return CheckpointBridge(storage, job_id, staging, serving_gate=_passing_serving_gate)


def test_checkpoint_bridge_publishes_only_completed_native_checkpoint(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "runs")
    staging = tmp_path / "staging"
    native = _write_native_checkpoint(staging, 50)

    bridge = _bridge(storage, "grpo-job", staging)
    assert bridge.publish_available() == []

    (staging / "latest_checkpointed_iteration.txt").write_text("50", encoding="utf-8")
    assert bridge.publish_available() == [50]
    assert bridge.publish_available() == []

    resume = latest_storage_resume_path(storage, "grpo-job")
    assert resume == tmp_path / "runs" / "grpo-job" / "ckpt" / "step_50" / "global_step_50"
    assert (resume / "actor" / "optim_world_size_1_rank_0.pt").read_bytes() == b"optimizer-state"
    assert not native.exists()

    # The persisted bridge state also prevents an expensive duplicate copy after
    # the sidecar itself is restarted.
    assert _bridge(storage, "grpo-job", staging).publish_available() == []


def test_checkpoint_bridge_runs_serving_gate_before_storage_publish(monkeypatch, tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "runs")
    staging = tmp_path / "staging"
    native = _write_native_checkpoint(staging, 50)
    (staging / "latest_checkpointed_iteration.txt").write_text("50", encoding="utf-8")
    calls: list[str] = []

    def passing_gate(path: Path, *, lora_rank: int, lora_alpha: int, evidence_path: Path) -> None:
        assert path == native
        assert (lora_rank, lora_alpha) == (4, 8)
        assert storage.checkpoint_paths("grpo-job") == []
        calls.append("gate")
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text('{"status":"completed"}\n', encoding="utf-8")

    original_save = storage.save_checkpoint

    def observed_save(*args: object) -> None:
        calls.append("storage")
        original_save(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(storage, "save_checkpoint", observed_save)
    bridge = CheckpointBridge(
        storage,
        "grpo-job",
        staging,
        lora_rank=4,
        lora_alpha=8,
        serving_gate=passing_gate,
    )

    assert bridge.publish_available() == [50]
    assert calls == ["gate", "storage"]
    assert (
        storage.root / "grpo-job" / "evidence" / "serveability" / "step_50.json"
    ).is_file()


def test_checkpoint_bridge_refuses_publication_when_serving_gate_fails(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "runs")
    staging = tmp_path / "staging"
    native = _write_native_checkpoint(staging, 50)
    (staging / "latest_checkpointed_iteration.txt").write_text("50", encoding="utf-8")

    def failing_gate(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("vLLM completion failed")

    bridge = CheckpointBridge(storage, "grpo-job", staging, serving_gate=failing_gate)
    with pytest.raises(CheckpointPublicationError, match="vLLM completion failed"):
        bridge.publish_available()

    assert storage.checkpoint_paths("grpo-job") == []
    assert native.is_dir()


def test_post_training_bridge_stores_candidate_without_running_gate(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "runs")
    staging = tmp_path / "staging"
    native = _write_native_checkpoint(staging, 50)
    (staging / "latest_checkpointed_iteration.txt").write_text("50", encoding="utf-8")

    def forbidden_gate(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("training-time candidate must not start vLLM")

    bridge = CheckpointBridge(
        storage,
        "grpo-job",
        staging,
        serving_gate=forbidden_gate,
        serving_gate_timing="post_training",
    )

    assert bridge.publish_available() == [50]
    assert bridge.has_candidate(50) is True
    assert storage.checkpoint_paths("grpo-job") == []
    candidate = storage.root / "grpo-job" / "artifacts" / "candidate-checkpoints" / "step_50"
    assert (candidate / "global_step_50" / "data.pt").is_file()
    assert not native.exists()


def test_post_training_bridge_gates_selected_candidate_before_publication(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "runs")
    staging = tmp_path / "staging"
    _write_native_checkpoint(staging, 100)
    (staging / "latest_checkpointed_iteration.txt").write_text("100", encoding="utf-8")
    calls: list[str] = []

    def passing_gate(path: Path, **kwargs: object) -> None:
        assert path.name == "global_step_100"
        assert storage.checkpoint_paths("grpo-job") == []
        calls.append("gate")
        evidence = Path(str(kwargs["evidence_path"]))
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text('{"status":"completed"}\n', encoding="utf-8")

    bridge = CheckpointBridge(
        storage,
        "grpo-job",
        staging,
        serving_gate=passing_gate,
        serving_gate_timing="post_training",
    )
    assert bridge.publish_available() == [100]

    restarted = CheckpointBridge(
        storage,
        "grpo-job",
        tmp_path / "finalizer",
        serving_gate=passing_gate,
        serving_gate_timing="post_training",
    )
    checkpoint = restarted.finalize_candidate(100)

    assert calls == ["gate"]
    assert checkpoint.name == "step_100"
    assert (checkpoint / "global_step_100" / "data.pt").is_file()
    assert (
        storage.root
        / "grpo-job"
        / "artifacts"
        / "serveability"
        / "step_100.json"
    ).is_file()


def test_post_training_failed_gate_leaves_candidate_unpublished(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "runs")
    staging = tmp_path / "staging"
    _write_native_checkpoint(staging, 100)
    (staging / "latest_checkpointed_iteration.txt").write_text("100", encoding="utf-8")

    bridge = CheckpointBridge(
        storage,
        "grpo-job",
        staging,
        serving_gate=_passing_serving_gate,
        serving_gate_timing="post_training",
    )
    assert bridge.publish_available() == [100]

    def failing_gate(*_args: object, **kwargs: object) -> None:
        evidence = Path(str(kwargs["evidence_path"]))
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text('{"status":"failed"}\n', encoding="utf-8")
        raise RuntimeError("exclusive serving smoke failed")

    finalizer = CheckpointBridge(
        storage,
        "grpo-job",
        tmp_path / "finalizer",
        serving_gate=failing_gate,
        serving_gate_timing="post_training",
    )
    with pytest.raises(CheckpointPublicationError, match="exclusive serving smoke failed"):
        finalizer.finalize_candidate(100)

    assert storage.checkpoint_paths("grpo-job") == []
    assert (
        storage.root
        / "grpo-job"
        / "artifacts"
        / "candidate-checkpoints"
        / "step_100"
    ).is_dir()


def test_checkpoint_retention_keeps_all_hf_exports_but_only_latest_native(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "runs")
    staging = tmp_path / "staging"
    bridge = _bridge(storage, "grpo-job", staging)

    _write_native_checkpoint(staging, 50)
    (staging / "latest_checkpointed_iteration.txt").write_text("50", encoding="utf-8")
    assert bridge.publish_available() == [50]

    _write_native_checkpoint(staging, 100)
    (staging / "latest_checkpointed_iteration.txt").write_text("100", encoding="utf-8")
    assert bridge.publish_available() == [100]

    older = storage.root / "grpo-job" / "ckpt" / "step_50" / "global_step_50"
    latest = storage.root / "grpo-job" / "ckpt" / "step_100" / "global_step_100"
    assert (older / "actor" / "huggingface" / "model.safetensors").read_bytes() == b"hf-weights"
    assert not (older / "actor" / "model_world_size_1_rank_0.pt").exists()
    assert not (older / "actor" / "optim_world_size_1_rank_0.pt").exists()
    assert not (older / "data.pt").exists()
    assert latest_storage_resume_path(storage, "grpo-job") == latest
    assert not (staging / "global_step_100").exists()


def test_latest_resume_skips_a_higher_hf_only_export(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "runs")
    complete_wrapper = tmp_path / "complete"
    complete = _write_native_checkpoint(complete_wrapper, 50)
    storage.save_checkpoint("grpo-job", 50, complete_wrapper)

    hf_only_wrapper = tmp_path / "hf-only"
    hf = hf_only_wrapper / "global_step_100" / "actor" / "huggingface"
    hf.mkdir(parents=True)
    (hf / "model.safetensors").write_bytes(b"heldout-only")
    storage.save_checkpoint("grpo-job", 100, hf_only_wrapper)

    assert latest_storage_resume_path(storage, "grpo-job") == (
        storage.root / "grpo-job" / "ckpt" / "step_50" / "global_step_50"
    )


def test_resume_quarantines_unpublished_staging_and_deletes_successful_staging(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "runs")
    staging = tmp_path / "staging"
    bridge = _bridge(storage, "grpo-job", staging)
    for step in (50, 100):
        _write_native_checkpoint(staging, step)
        (staging / "latest_checkpointed_iteration.txt").write_text(str(step), encoding="utf-8")
        assert bridge.publish_available() == [step]

    _write_native_checkpoint(staging, 100)
    _write_native_checkpoint(staging, 150)
    (staging / "latest_checkpointed_iteration.txt").write_text("150", encoding="utf-8")

    quarantined = bridge.prepare_resume()

    assert quarantined == [
        storage.root / "grpo-job" / "evidence" / "failed-staging" / "global_step_150"
    ]
    assert not (staging / "global_step_100").exists()
    assert not (staging / "global_step_150").exists()
    assert not (staging / "latest_checkpointed_iteration.txt").exists()
    assert latest_storage_resume_path(storage, "grpo-job").name == "global_step_100"


def test_checkpoint_capacity_budget_accepts_and_rejects_with_explicit_arithmetic(
    monkeypatch, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path / "runs")
    staging = tmp_path / "staging"
    bridge = _bridge(storage, "grpo-job", staging)
    _write_native_checkpoint(staging, 50)
    (staging / "latest_checkpointed_iteration.txt").write_text("50", encoding="utf-8")
    assert bridge.publish_available() == [50]

    monkeypatch.setattr(
        checkpoint_bridge_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=100_000),
    )
    budget = bridge.checkpoint_budget(total_steps=100, checkpoint_every=50, model_path="unused")
    assert budget.checkpoint_count == 2
    assert budget.projected_peak_bytes == 4 * budget.hf_export_bytes + 3 * budget.full_checkpoint_bytes
    assert budget.projected_peak_bytes <= budget.allowed_bytes

    monkeypatch.setattr(
        checkpoint_bridge_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=1),
    )
    with pytest.raises(CheckpointCapacityError, match="checkpoint_count=2"):
        bridge.checkpoint_budget(total_steps=100, checkpoint_every=50, model_path="unused")


def test_failed_checkpoint_publication_quarantines_staging_and_writes_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path / "runs")
    staging = tmp_path / "staging"
    native = _write_native_checkpoint(staging, 50)
    (staging / "latest_checkpointed_iteration.txt").write_text("50", encoding="utf-8")
    bridge = _bridge(storage, "grpo-job", staging)

    def fail_save(*_args, **_kwargs) -> None:
        raise OSError(122, "Disk quota exceeded")

    monkeypatch.setattr(storage, "save_checkpoint", fail_save)
    with pytest.raises(CheckpointPublicationError) as raised:
        bridge.publish_available()

    error = raised.value
    assert error.native_checkpoint == native
    assert native.exists()
    _preserve_checkpoint_failure(storage, "grpo-job", bridge, error)

    assert error.quarantined_path == (
        storage.root / "grpo-job" / "evidence" / "failed-staging" / "global_step_50"
    )
    assert not native.exists()
    payload = json.loads(
        (storage.root / "grpo-job" / "evidence" / "checkpoint-publication-failure.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["cause_type"] == "OSError"
    assert payload["quarantined_staging_path"] == str(error.quarantined_path)


def test_metric_bridge_normalizes_validation_and_deduplicates_resume(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "runs")
    logger = tmp_path / "verl.jsonl"
    logger.write_text(
        "\n".join(
            (
                json.dumps({"step": 0, "data": {"val-core/nl2sql_v1/acc/mean@1": 0.25}}),
                json.dumps(
                    {
                        "step": 1,
                        "data": {"critic/score/mean": 0.2, "actor/entropy": 1.1},
                    }
                ),
                json.dumps(
                    {
                        "step": 2,
                        "data": {
                            "critic/score/mean": 0.5,
                            "actor/entropy": 0.9,
                            "val-core/nl2sql_v1/acc/mean@1": 0.6,
                        },
                    }
                ),
                '{"step": 3',
            )
        ),
        encoding="utf-8",
    )

    bridge = VerlMetricBridge(storage, "grpo-job", logger)
    assert [metric.step for metric in bridge.drain()] == [1, 2]
    assert bridge.drain() == []

    metrics_path = tmp_path / "runs" / "grpo-job" / "metrics.jsonl"
    first_records = [json.loads(line) for line in metrics_path.read_text(encoding="utf-8").splitlines()]
    assert [(record["step"], record["pass_at_1"]) for record in first_records] == [(1, 0.25), (2, 0.6)]

    # verl opens its file logger in write mode during a resumed invocation.
    logger.write_text(
        "\n".join(
            (
                json.dumps({"step": 2, "data": {"val-core/nl2sql_v1/acc/mean@1": 0.6}}),
                json.dumps(
                    {
                        "step": 3,
                        "data": {"critic/score/mean": 0.7, "actor/entropy": 0.8},
                    }
                ),
            )
        ),
        encoding="utf-8",
    )
    resumed = VerlMetricBridge(storage, "grpo-job", logger)
    assert [metric.step for metric in resumed.drain()] == [3]
    all_records = [json.loads(line) for line in metrics_path.read_text(encoding="utf-8").splitlines()]
    assert [record["step"] for record in all_records] == [1, 2, 3]
    assert all_records[-1]["pass_at_1"] == 0.6


def test_grpo_config_builds_storage_only_resume_command(tmp_path: Path) -> None:
    config = GrpoSmokeConfig.load()
    resume = tmp_path / "runs" / "job" / "ckpt" / "step_50" / "global_step_50"
    command = build_verl_command(
        config=config,
        job_id="job",
        train_file=tmp_path / "train.parquet",
        validation_file=tmp_path / "validation.parquet",
        staging_dir=tmp_path / "staging",
        resume_path=resume,
        steps_per_epoch=10,
        python="python",
    )

    assert command[:3] == ["python", "-m", "verl.trainer.main_ppo"]
    assert "algorithm.adv_estimator=grpo" in command
    assert "actor_rollout_ref.model.lora_rank=16" in command
    assert "+actor_rollout_ref.model.override_config.attn_implementation=sdpa" in command
    assert "actor_rollout_ref.rollout.tensor_model_parallel_size=1" in command
    assert "data.val_batch_size=1" in command
    assert "trainer.save_freq=50" in command
    assert "trainer.resume_mode=resume_path" in command
    assert f"trainer.resume_from_path={resume}" in command
    assert "trainer.resume_mode=disable" not in command

    fallback = config.with_l4_fallback()
    assert (
        fallback.train_batch_size,
        fallback.max_response_length,
        fallback.rollout_gpu_memory_utilization,
    ) == (2, 256, 0.35)
    assert config.with_total_steps(2).checkpoint_every == 2


def test_blackwell_smoke_config_is_bounded_single_gpu_probe(tmp_path: Path) -> None:
    config = GrpoSmokeConfig.load("grpo_v1_1p5b_blackwell_smoke")
    command = build_verl_command(
        config=config,
        job_id="blackwell-smoke",
        train_file=tmp_path / "train.parquet",
        validation_file=tmp_path / "validation.parquet",
        staging_dir=tmp_path / "staging",
        resume_path=None,
        steps_per_epoch=10,
        python="python",
    )

    assert config.model_path == "Qwen/Qwen2.5-1.5B-Instruct"
    assert config.total_steps == 30
    assert config.rollout_n == 8
    assert config.checkpoint_every == 10
    assert config.rollout_gpu_memory_utilization == 0.50
    assert config.enforce_eager is True
    assert config.vllm_attention_backend is None
    assert "trainer.n_gpus_per_node=1" in command
    assert "actor_rollout_ref.rollout.tensor_model_parallel_size=1" in command
    assert "actor_rollout_ref.rollout.enforce_eager=true" in command
    assert not any("VLLM_ATTENTION_BACKEND=" in argument for argument in command)
    assert "trainer.total_training_steps=30" in command
    assert "trainer.resume_mode=disable" in command


def test_h100_smoke_config_is_bounded_single_gpu_probe(tmp_path: Path) -> None:
    config = GrpoSmokeConfig.load("grpo_v1_1p5b_h100_smoke")
    command = build_verl_command(
        config=config,
        job_id="h100-smoke",
        train_file=tmp_path / "train.parquet",
        validation_file=tmp_path / "validation.parquet",
        staging_dir=tmp_path / "staging",
        resume_path=None,
        steps_per_epoch=10,
        python="python",
    )

    assert config.model_path == "Qwen/Qwen2.5-1.5B-Instruct"
    assert config.total_steps == 30
    assert config.rollout_n == 8
    assert config.rollout_gpu_memory_utilization == 0.45
    assert config.enforce_eager is False
    assert config.vllm_attention_backend is None
    assert "+actor_rollout_ref.model.override_config.attn_implementation=sdpa" in command
    assert "trainer.n_gpus_per_node=1" in command
    assert "actor_rollout_ref.rollout.tensor_model_parallel_size=1" in command
    assert "actor_rollout_ref.rollout.enforce_eager=false" in command
    assert not any("VLLM_ATTENTION_BACKEND=" in argument for argument in command)


def test_h100_diagnostic_environment_reaches_ray_workers(monkeypatch, tmp_path: Path) -> None:
    expected = {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "VLLM_LOGGING_LEVEL": "INFO",
        "RAY_DEDUP_LOGS": "0",
        "PYTHONFAULTHANDLER": "1",
    }
    for name, value in expected.items():
        monkeypatch.setenv(name, value)

    command = build_verl_command(
        config=GrpoSmokeConfig.load("grpo_v1_1p5b_h100_smoke"),
        job_id="h100-smoke",
        train_file=tmp_path / "train.parquet",
        validation_file=tmp_path / "validation.parquet",
        staging_dir=tmp_path / "staging",
        resume_path=None,
        steps_per_epoch=10,
        python="python",
    )

    for name, value in expected.items():
        assert f"+ray_kwargs.ray_init.runtime_env.env_vars.{name}='{value}'" in command


def test_total_epoch_guard_derives_or_rejects_before_verl_launch(tmp_path: Path) -> None:
    default = GrpoSmokeConfig.load()
    assert default.resolve_total_epochs(10) == 12

    main = GrpoSmokeConfig.load("grpo_v1_1p5b_h100_main")
    assert main.resolve_total_epochs(12) == 40
    command = build_verl_command(
        config=main,
        job_id="m3",
        train_file=tmp_path / "train.parquet",
        validation_file=tmp_path / "validation.parquet",
        staging_dir=tmp_path / "staging",
        resume_path=None,
        steps_per_epoch=12,
        python="python",
    )
    assert "trainer.total_epochs=40" in command
    assert "trainer.total_epochs=10" not in command

    unsafe = replace(main, total_epochs=10)
    with pytest.raises(ValueError, match="would cap trainer.total_training_steps"):
        build_verl_command(
            config=unsafe,
            job_id="unsafe",
            train_file=tmp_path / "train.parquet",
            validation_file=tmp_path / "validation.parquet",
            staging_dir=tmp_path / "staging",
            resume_path=None,
            steps_per_epoch=12,
            python="python",
        )


def test_runtime_evidence_captures_pip_driver_and_diagnostic_environment(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    def runner(command, **_kwargs):
        if command[0] == "nvidia-smi":
            return subprocess.CompletedProcess(command, 0, "H100, 570.195.03, 81559 MiB\n", "")
        return subprocess.CompletedProcess(command, 0, "verl==0.8.0\n", "")

    evidence = capture_runtime_evidence(tmp_path / "job", runner=runner)
    content = evidence.read_text(encoding="utf-8")

    assert "HF_HUB_OFFLINE=1" in content
    assert "command=" in content
    assert "verl==0.8.0" in content
    assert "H100, 570.195.03, 81559 MiB" in content


def test_dataset_rows_and_curve_artifact_are_portable(tmp_path: Path) -> None:
    rows = build_verl_rows(
        [
            {
                "id": "v1-001",
                "prompt": "Return SQL",
                "schema_sql": "CREATE TABLE items (id INTEGER);",
                "expected_results": [[1]],
            }
        ]
    )
    assert rows[0]["data_source"] == "nl2sql_v1"
    assert rows[0]["prompt"] == [{"role": "user", "content": "Return SQL"}]
    assert json.loads(rows[0]["reward_model"]["ground_truth"])["expected_results"] == [[1]]

    metrics = tmp_path / "metrics.jsonl"
    metrics.write_text(
        "\n".join(
            (
                json.dumps({"step": 1, "reward_mean": 0.2, "pass_at_1": 0.1, "entropy": 1.2}),
                json.dumps({"step": 2, "reward_mean": 0.5, "pass_at_1": 0.4, "entropy": 0.9}),
            )
        ),
        encoding="utf-8",
    )
    curve = render_curve(metrics, tmp_path / "curve.png")
    assert curve.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_final_artifacts_are_manifest_and_curve_not_weights(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "runs")
    config = GrpoSmokeConfig.load().with_total_steps(2)
    final_checkpoint = tmp_path / "runs" / "job" / "ckpt" / "step_2" / "global_step_2"
    final_checkpoint.mkdir(parents=True)
    storage.append_metrics(
        "job",
        {"job_id": "job", "step": 2, "reward_mean": 0.5, "pass_at_1": 0.4, "entropy": 0.9},
    )

    _publish_final_artifacts(storage, "job", config, final_checkpoint)

    manifest = tmp_path / "runs" / "job" / "artifacts" / "final" / "model.txt"
    assert "completed_step: 2" in manifest.read_text(encoding="utf-8")
    assert "weights remain in ckpt/" in manifest.read_text(encoding="utf-8")
    assert (tmp_path / "runs" / "job" / "artifacts" / "curve.png").read_bytes().startswith(b"\x89PNG")
