from __future__ import annotations

import json
from pathlib import Path

from core.storage.local import LocalStorage
from trainer import grpo_dataset
from trainer.grpo_config import GrpoSmokeConfig
from trainer.grpo_reward import compute_random_score
from trainer.grpo_train import EntropyBrake, EntropyBrakeDecision, _publish_entropy_brake, build_verl_command


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
        python="python",
    )
    assert "reward.custom_reward_function.name=compute_random_score" in control_command


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
