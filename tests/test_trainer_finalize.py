from pathlib import Path

from core.storage.local import LocalStorage
from trainer import finalize_checkpoint
from trainer.grpo_config import GrpoSmokeConfig


def test_post_training_finalizer_publishes_final_artifacts_after_gate(
    monkeypatch, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path / "runs")
    checkpoint = storage.root / "job" / "ckpt" / "step_100"
    (checkpoint / "global_step_100").mkdir(parents=True)
    events: list[str] = []

    class Bridge:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def finalize_candidate(self, step: int) -> Path:
            assert step == 100
            events.append("gate")
            return checkpoint

    def publish(*_args: object, **_kwargs: object) -> None:
        events.append("final")

    monkeypatch.setattr(finalize_checkpoint, "CheckpointBridge", Bridge)
    monkeypatch.setattr(finalize_checkpoint, "_publish_final_artifacts", publish)

    status = finalize_checkpoint.run(
        "job",
        GrpoSmokeConfig.load("grpo_v1_0p5b_p2"),
        storage=storage,
    )

    assert status == 0
    assert events == ["gate", "final"]


def test_post_training_finalizer_persists_unexpected_failure(monkeypatch, tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "runs")

    class Bridge:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def finalize_candidate(self, _step: int) -> Path:
            raise OSError("candidate download interrupted")

        def quarantine_failed_native(self, _path: Path) -> None:
            return None

    monkeypatch.setattr(finalize_checkpoint, "CheckpointBridge", Bridge)

    status = finalize_checkpoint.run(
        "job",
        GrpoSmokeConfig.load("grpo_v1_0p5b_p2"),
        storage=storage,
    )

    assert status == 74
    failure = storage.root / "job" / "artifacts" / "checkpoint-publication-failure.json"
    assert failure.is_file()
    assert "candidate download interrupted" in failure.read_text(encoding="utf-8")
