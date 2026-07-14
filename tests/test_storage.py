import json
from pathlib import Path

from core.storage.local import LocalStorage


def test_checkpoints_publish_by_rename_replace_idempotently_and_resume(tmp_path, monkeypatch):
    storage = LocalStorage(tmp_path / "runs")
    first = tmp_path / "first.txt"
    first.write_text("first", encoding="utf-8")

    rename_calls: list[tuple[Path, Path]] = []
    import core.storage.local as local_module

    real_rename = local_module.os.rename

    def record_rename(source, destination):
        rename_calls.append((Path(source), Path(destination)))
        real_rename(source, destination)

    monkeypatch.setattr(local_module.os, "rename", record_rename)
    storage.save_checkpoint("job-a", 3, first)

    checkpoint = storage.load_latest_checkpoint("job-a")
    assert checkpoint == tmp_path / "runs" / "job-a" / "ckpt" / "step_3"
    assert (checkpoint / "first.txt").read_text(encoding="utf-8") == "first"
    assert any(source.name.endswith(".tmp") and destination == checkpoint for source, destination in rename_calls)

    replacement = tmp_path / "replacement.txt"
    replacement.write_text("replacement", encoding="utf-8")
    storage.save_checkpoint("job-a", 3, replacement)
    assert (checkpoint / "replacement.txt").read_text(encoding="utf-8") == "replacement"
    assert not (checkpoint / "first.txt").exists()
    assert not list(checkpoint.parent.glob("*.tmp"))

    storage.save_checkpoint("job-a", 12, replacement)
    assert storage.load_latest_checkpoint("job-a").name == "step_12"


def test_metrics_are_append_only_and_artifacts_round_trip(tmp_path):
    storage = LocalStorage(tmp_path / "runs")
    storage.append_metrics("job-a", {"step": 1, "reward_mean": 0.2})
    metrics_path = tmp_path / "runs" / "job-a" / "metrics.jsonl"
    first_contents = metrics_path.read_text(encoding="utf-8")

    storage.append_metrics("job-a", {"step": 2, "reward_mean": 0.4})
    lines = metrics_path.read_text(encoding="utf-8").splitlines()
    assert metrics_path.read_text(encoding="utf-8").startswith(first_contents)
    assert [json.loads(line)["step"] for line in lines] == [1, 2]

    model = tmp_path / "model.txt"
    model.write_text("fake model", encoding="utf-8")
    storage.put_artifact("job-a", "final/model.txt", model)
    downloaded = storage.get_artifact("job-a", "final/model.txt", tmp_path / "downloaded.txt")
    assert downloaded.read_text(encoding="utf-8") == "fake model"
