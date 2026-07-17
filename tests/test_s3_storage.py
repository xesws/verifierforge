import hashlib
import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from core.storage.local import LocalStorage
from core.storage.s3 import S3Storage


@pytest.fixture
def s3_client(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="verifierforge-storage-tests")
        yield client


def _storage(tmp_path: Path, client) -> S3Storage:
    return S3Storage(
        "verifierforge-storage-tests",
        prefix="test-prefix",
        cache_root=tmp_path / "cache",
        client=client,
    )


def test_s3_checkpoint_publish_overwrite_and_resume(tmp_path, s3_client):
    storage = _storage(tmp_path, s3_client)
    first = tmp_path / "first.txt"
    first.write_text("first", encoding="utf-8")

    storage.save_checkpoint("job-a", 3, first)
    checkpoint = storage.load_latest_checkpoint("job-a")
    assert checkpoint == tmp_path / "cache" / "job-a" / "ckpt" / "step_3"
    assert (checkpoint / "first.txt").read_text(encoding="utf-8") == "first"

    replacement = tmp_path / "replacement.txt"
    replacement.write_text("replacement", encoding="utf-8")
    storage.save_checkpoint("job-a", 3, replacement)
    assert (checkpoint / "replacement.txt").read_text(encoding="utf-8") == "replacement"
    assert not (checkpoint / "first.txt").exists()

    storage.save_checkpoint("job-a", 12, replacement)
    assert storage.load_latest_checkpoint("job-a").name == "step_12"
    assert [step for step, _ in storage.checkpoint_paths("job-a")] == [3, 12]


def test_s3_checkpoint_failure_never_publishes_a_partial_manifest(tmp_path, s3_client, monkeypatch):
    storage = _storage(tmp_path, s3_client)
    source = tmp_path / "checkpoint"
    source.mkdir()
    (source / "one.txt").write_text("one", encoding="utf-8")
    (source / "two.txt").write_text("two", encoding="utf-8")

    real_put_file = storage._put_file
    calls = 0

    def fail_second_upload(key, path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated interrupted upload")
        real_put_file(key, path)

    monkeypatch.setattr(storage, "_put_file", fail_second_upload)
    with pytest.raises(OSError, match="simulated interrupted upload"):
        storage.save_checkpoint("job-a", 5, source)

    assert storage.load_latest_checkpoint("job-a") is None
    objects = s3_client.list_objects_v2(Bucket=storage.bucket, Prefix="test-prefix/jobs/job-a/ckpt/")
    keys = [entry["Key"] for entry in objects.get("Contents", [])]
    assert keys
    assert not any(key.endswith("step_5/manifest.json") for key in keys)


def test_s3_metrics_are_append_only_and_artifacts_round_trip(tmp_path, s3_client):
    storage = _storage(tmp_path, s3_client)
    for step in range(1, 51):
        storage.append_metrics("job-a", {"step": step, "reward_mean": step / 100})
    assert [record["step"] for record in storage.read_metrics("job-a")] == list(range(1, 51))

    metric_keys = list(storage._list_keys("test-prefix/jobs/job-a/metrics.jsonl/"))
    assert len(metric_keys) == 50
    assert all(key.endswith(".json") for key in metric_keys)

    model = tmp_path / "model.txt"
    model.write_text("fake model", encoding="utf-8")
    storage.put_artifact("job-a", "final/model.txt", model)
    downloaded = storage.get_artifact("job-a", "final/model.txt", tmp_path / "downloaded.txt")
    assert downloaded.read_text(encoding="utf-8") == "fake model"

    report = tmp_path / "report"
    report.mkdir()
    (report / "nested").mkdir()
    (report / "nested" / "result.json").write_text('{"pass_at_1":0.78}', encoding="utf-8")
    storage.put_artifact("job-a", "reports/heldout", report)
    directory = tmp_path / "artifact-download"
    directory.mkdir()
    restored = storage.get_artifact("job-a", "reports/heldout", directory)
    assert restored == directory / "heldout"
    assert (restored / "nested" / "result.json").read_text(encoding="utf-8") == '{"pass_at_1":0.78}'


def test_s3_cache_reconstruction_verifies_checkpoint_identity(tmp_path, s3_client):
    storage = _storage(tmp_path, s3_client)
    source = tmp_path / "checkpoint"
    source.mkdir()
    payload = b"durable checkpoint\n"
    (source / "state.bin").write_bytes(payload)
    storage.save_checkpoint("job-a", 7, source)
    storage.append_metrics("job-a", {"step": 1, "reward_mean": 0.2})
    storage.append_metrics("job-a", {"step": 2, "reward_mean": 0.4})

    cache = tmp_path / "new-cache"
    reader = S3Storage(storage.bucket, prefix=storage.prefix, cache_root=cache, client=s3_client)
    restored = reader.load_latest_checkpoint("job-a")
    assert restored is not None
    assert (restored / "state.bin").read_bytes() == payload
    assert hashlib.sha256((restored / "state.bin").read_bytes()).hexdigest() == hashlib.sha256(payload).hexdigest()

    metrics_path = cache / "job-a" / "metrics.jsonl"
    assert [json.loads(line)["step"] for line in metrics_path.read_text(encoding="utf-8").splitlines()] == [1, 2]


def test_local_storage_selects_s3_only_when_explicit(tmp_path, s3_client, monkeypatch):
    monkeypatch.setenv("VF_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("VF_S3_BUCKET", "verifierforge-storage-tests")
    monkeypatch.setenv("VF_S3_PREFIX", "selected")
    selected = LocalStorage(tmp_path / "selected-cache")
    assert isinstance(selected, S3Storage)
    assert selected.root == tmp_path / "selected-cache"

    selected.append_metrics("job-a", {"step": 1})
    assert selected.read_metrics("job-a") == [{"step": 1}]
