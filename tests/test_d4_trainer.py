from __future__ import annotations

import json
from pathlib import Path

from core.storage.local import LocalStorage
from trainer import grpo_dataset
from trainer.grpo_config import GrpoSmokeConfig
from trainer.grpo_reward import compute_random_score
from trainer.grpo_train import EntropyBrake, EntropyBrakeDecision, _publish_entropy_brake, build_verl_command
from trainer.export_compat import SERVEABLE_MANIFEST
from trainer.heldout_eval import (
    CheckpointResult,
    HeldoutEvaluationError,
    eligible_checkpoints,
    select_best_checkpoint,
    verify_evaluation_evidence,
)


def test_d4_main_and_control_configs_are_explicit_and_separate(tmp_path: Path) -> None:
    main = GrpoSmokeConfig.load("grpo_v1_1p5b_h100_main")
    control = GrpoSmokeConfig.load("grpo_v1_0p5b_random_control")

    assert (main.total_steps, main.rollout_n, main.checkpoint_every) == (400, 8, 50)
    assert main.dataset_mode == "frozen_training_pool"
    assert main.entropy_brake is True
    assert main.checkpoint_save_contents.endswith("'hf_model']")
    assert main.reward_function_name == "compute_score"
    assert (control.total_steps, control.rollout_n, control.checkpoint_every) == (200, 8, 50)
    assert control.dataset_mode == "frozen_training_pool"
    assert control.reward_function_name == "compute_random_score"
    assert control.entropy_brake is False

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
    assert "actor_rollout_ref.actor.checkpoint.save_contents=['model','optimizer','extra','hf_model']" in command
    assert "reward.custom_reward_function.name=compute_score" in command

    control_command = build_verl_command(
        config=control,
        job_id="m4",
        train_file=tmp_path / "train.parquet",
        validation_file=tmp_path / "validation.parquet",
        staging_dir=tmp_path / "staging",
        resume_path=None,
        steps_per_epoch=12,
        python="python",
    )
    assert "reward.custom_reward_function.name=compute_random_score" in control_command


def test_p2_profile_is_frozen_s3_delivery_shape() -> None:
    config = GrpoSmokeConfig.load("grpo_v1_0p5b_p2")

    assert config.model_path == "Qwen/Qwen2.5-0.5B-Instruct"
    assert (config.total_steps, config.rollout_n, config.checkpoint_every) == (100, 8, 50)
    assert config.dataset_mode == "frozen_training_pool"
    assert config.reward_function_name == "compute_score"
    assert config.save_hf_model is True


def test_frozen_training_mode_uses_all_pool_rows_and_no_heldout(monkeypatch, tmp_path: Path) -> None:
    written: dict[str, list[dict[str, object]]] = {}

    def fake_write(rows, destination: Path) -> Path:
        written[destination.name] = list(rows)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("placeholder", encoding="utf-8")
        return destination

    monkeypatch.setattr(grpo_dataset, "write_parquet", fake_write)
    paths = grpo_dataset.prepare_v1_inputs(
        tmp_path / "runs", "m3", dataset_mode="frozen_training_pool"
    )

    assert paths.train.is_file()
    assert paths.validation.is_file()
    assert paths.train_rows == 50
    assert paths.steps_per_epoch(4) == 12
    assert len(written["train.parquet"]) == 50
    assert len(written["validation.parquet"]) == 10
    train_ids = [str(row["extra_info"]["case_id"]) for row in written["train.parquet"]]
    validation_ids = [str(row["extra_info"]["case_id"]) for row in written["validation.parquet"]]
    assert validation_ids == sorted(train_ids)[:10]


def test_entropy_brake_waits_until_step_20_then_requires_ten_consecutive_values() -> None:
    brake = EntropyBrake()
    for step in range(1, 11):
        assert brake.observe(step=step, entropy=1.0) is None
    for step in range(11, 21):
        assert brake.observe(step=step, entropy=0.0) is None
    for step in range(21, 30):
        assert brake.observe(step=step, entropy=0.24) is None

    decision = brake.observe(step=30, entropy=0.24)

    assert isinstance(decision, EntropyBrakeDecision)
    assert decision.trigger_step == 30
    assert decision.baseline_median == 1.0
    assert decision.threshold == 0.25
    assert [step for step, _ in decision.below_threshold_window] == list(range(21, 31))


def test_entropy_brake_artifact_marks_early_stop_without_a_final_model(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "runs")
    decision = EntropyBrakeDecision(
        trigger_step=30,
        baseline_entropies=(1.0,) * 10,
        baseline_median=1.0,
        threshold=0.25,
        below_threshold_window=tuple((step, 0.2) for step in range(21, 31)),
    )

    evidence = _publish_entropy_brake(
        storage,
        "m3",
        GrpoSmokeConfig.load("grpo_v1_1p5b_h100_main"),
        decision,
    )

    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["status"] == "early_stopped"
    assert payload["latest_storage_checkpoint_step"] is None
    assert (tmp_path / "runs" / "m3" / "early_stopped").is_file()
    assert (tmp_path / "runs" / "m3" / "artifacts" / "entropy-brake.json").is_file()
    assert not (tmp_path / "runs" / "m3" / "artifacts" / "final" / "model.txt").exists()


def test_random_control_reward_is_deterministic_and_never_constructs_a_verifier(monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("random control must not invoke the verifier")

    monkeypatch.setattr("trainer.grpo_reward.NL2SQLVerifier", forbidden)
    first = compute_random_score("nl2sql_v1", "SELECT 1", {"example": 1})
    assert first == compute_random_score("nl2sql_v1", "SELECT 1", {"example": 1})
    draws = {
        compute_random_score("nl2sql_v1", f"SELECT {index}", {"example": index})["score"]
        for index in range(32)
    }
    assert draws == {0.0, 1.0}


def test_heldout_evaluator_requires_exported_hf_checkpoint_and_tie_breaks_lowest_step(
    tmp_path: Path,
) -> None:
    hf = (
        tmp_path
        / "m3"
        / "ckpt"
        / "step_50"
        / "global_step_50"
        / "actor"
        / "huggingface"
    )
    hf.mkdir(parents=True)
    (hf / "model.safetensors").write_bytes(b"weights")

    checkpoints = eligible_checkpoints(tmp_path, "m3")

    assert [(checkpoint.step, checkpoint.hf_path) for checkpoint in checkpoints] == [(50, hf)]
    first = CheckpointResult(50, "a", "b", "c", {"pass_at_1": 0.6}, "completed", None)
    second = CheckpointResult(100, "d", "e", "f", {"pass_at_1": 0.6}, "completed", None)
    assert select_best_checkpoint([second, first]) == first

    (hf / "model.safetensors").unlink()
    try:
        eligible_checkpoints(tmp_path, "m3")
    except HeldoutEvaluationError as error:
        assert "lacks an exported" in str(error)
    else:  # pragma: no cover - documents the required failure boundary.
        raise AssertionError("missing HF export must not be evaluated")


def test_heldout_evaluator_can_require_a_completed_serveable_sibling(tmp_path: Path) -> None:
    native = tmp_path / "m3" / "ckpt" / "step_50" / "global_step_50"
    raw = native / "actor" / "huggingface"
    raw.mkdir(parents=True)
    (raw / "model.safetensors").write_bytes(b"raw")

    try:
        eligible_checkpoints(tmp_path, "m3", require_serveable=True)
    except HeldoutEvaluationError as error:
        assert "completed serveable" in str(error)
    else:  # pragma: no cover - documents the required failure boundary.
        raise AssertionError("raw PEFT export must not bypass the serving gate")

    serveable = native / "actor" / "serveable_huggingface"
    serveable.mkdir()
    (serveable / "config.json").write_text("{}", encoding="utf-8")
    (serveable / "model.safetensors.index.json").write_text("{}", encoding="utf-8")
    (serveable / "model.safetensors").write_bytes(b"merged")
    (serveable / SERVEABLE_MANIFEST).write_text("{}", encoding="utf-8")

    checkpoints = eligible_checkpoints(tmp_path, "m3", require_serveable=True)
    assert [(checkpoint.step, checkpoint.hf_path) for checkpoint in checkpoints] == [(50, serveable)]


def test_heldout_evaluator_rejects_mismatched_or_incomplete_sample_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    import trainer.heldout_eval as heldout

    dataset = tmp_path / "heldout.jsonl"
    dataset.write_text('{"id":"one"}\n', encoding="utf-8")
    samples = tmp_path / "samples.jsonl"
    samples.write_text("sample\n", encoding="utf-8")
    evidence = tmp_path / "evidence.json"
    monkeypatch.setattr(heldout, "HELDOUT_PATH", dataset)
    monkeypatch.setattr(heldout, "SAMPLE_COUNT", 1)
    evidence.write_text(
        json.dumps(
            {
                "status": "completed",
                "mode": "reference",
                "input_sha256": heldout._sha256_file(dataset),
                "candidate_count": 60,
                "k": 8,
                "sample_count": 1,
                "sample_evidence": {"sample_count": 1, "sha256": heldout._sha256_file(samples)},
                "verifier": {"version": 2},
                "pass_at_1": 0.6,
                "pass_at_8": 0.8,
                "mixed_fraction": 0.4,
            }
        ),
        encoding="utf-8",
    )

    assert verify_evaluation_evidence(evidence, samples) == {
        "pass_at_1": 0.6,
        "pass_at_8": 0.8,
        "mixed_fraction": 0.4,
    }

    evidence.write_text("{}", encoding="utf-8")
    try:
        verify_evaluation_evidence(evidence, samples)
    except HeldoutEvaluationError as error:
        assert "not a completed" in str(error)
    else:  # pragma: no cover - documents the required failure boundary.
        raise AssertionError("incomplete evidence must not be selected")
