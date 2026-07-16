import json
from pathlib import Path

from core.storage.local import LocalStorage
from trainer.checkpoint_bridge import CheckpointBridge, latest_storage_resume_path
from trainer.grpo_config import GrpoSmokeConfig
from trainer.grpo_dataset import build_verl_rows
from trainer.grpo_train import _publish_final_artifacts, build_verl_command
from trainer.metric_bridge import VerlMetricBridge
from trainer.plot_metrics import render_curve


def test_checkpoint_bridge_publishes_only_completed_native_checkpoint(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "runs")
    staging = tmp_path / "staging"
    native = staging / "global_step_50"
    (native / "actor").mkdir(parents=True)
    (native / "actor" / "state.txt").write_text("model + optimizer", encoding="utf-8")
    (native / "data.pt").write_text("dataloader state", encoding="utf-8")

    bridge = CheckpointBridge(storage, "grpo-job", staging)
    assert bridge.publish_available() == []

    (staging / "latest_checkpointed_iteration.txt").write_text("50", encoding="utf-8")
    assert bridge.publish_available() == [50]
    assert bridge.publish_available() == []

    resume = latest_storage_resume_path(storage, "grpo-job")
    assert resume == tmp_path / "runs" / "grpo-job" / "ckpt" / "step_50" / "global_step_50"
    assert (resume / "actor" / "state.txt").read_text(encoding="utf-8") == "model + optimizer"

    # The persisted bridge state also prevents an expensive duplicate copy after
    # the sidecar itself is restarted.
    assert CheckpointBridge(storage, "grpo-job", staging).publish_available() == []


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
        python="python",
    )

    assert config.model_path == "Qwen/Qwen2.5-1.5B-Instruct"
    assert config.total_steps == 30
    assert config.rollout_n == 8
    assert config.checkpoint_every == 10
    assert config.rollout_gpu_memory_utilization == 0.50
    assert "trainer.n_gpus_per_node=1" in command
    assert "actor_rollout_ref.rollout.tensor_model_parallel_size=1" in command
    assert "trainer.total_training_steps=30" in command
    assert "trainer.resume_mode=disable" in command


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
