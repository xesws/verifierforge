from __future__ import annotations

from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from core.storage.s3 import S3Storage
from scripts.s3_native_recovery import NativeCheckpointRecoveryError, publish_native_checkpoint


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> S3Storage:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="verifierforge-native-recovery")
        yield S3Storage(
            "verifierforge-native-recovery",
            prefix="recovery",
            cache_root=tmp_path / "cache",
            client=client,
        )


def _native_checkpoint(root: Path, step: int = 50) -> Path:
    native = root / f"global_step_{step}"
    actor = native / "actor"
    actor.mkdir(parents=True)
    (native / "data.pt").write_bytes(b"dataloader state")
    (actor / "model_world_size_1_rank_0.pt").write_bytes(b"model state")
    (actor / "optim_world_size_1_rank_0.pt").write_bytes(b"optimizer state")
    (actor / "extra_state_world_size_1_rank_0.pt").write_bytes(b"extra state")
    (actor / "huggingface").mkdir()
    (actor / "huggingface" / "config.json").write_text("{}", encoding="utf-8")
    return native


def test_native_recovery_publishes_resumable_wrapper_and_artifact(tmp_path: Path, storage: S3Storage) -> None:
    native = _native_checkpoint(tmp_path)
    prior_log = tmp_path / "prior.log"
    prior_log.write_text("checkpoint gate evidence\n", encoding="utf-8")
    evidence = tmp_path / "evidence.json"

    result = publish_native_checkpoint(
        storage,
        job_id="recover-job",
        step=50,
        native_checkpoint=native,
        evidence_path=evidence,
        prior_log=prior_log,
    )

    restored = storage.load_latest_checkpoint("recover-job")
    assert result["status"] == "published"
    assert result["source"] == result["restored"]
    assert restored is not None
    assert (restored / "global_step_50" / "actor" / "model_world_size_1_rank_0.pt").read_bytes() == b"model state"
    assert storage.get_artifact("recover-job", "evidence/s3-native-recovery-step-50.json", tmp_path / "out").is_file()
    assert storage.get_artifact("recover-job", "evidence/s3-first-attempt-train.log", tmp_path / "out-log").read_text(encoding="utf-8") == "checkpoint gate evidence\n"


def test_native_recovery_rejects_incomplete_or_wrongly_named_checkpoint(tmp_path: Path, storage: S3Storage) -> None:
    incomplete = tmp_path / "global_step_50"
    incomplete.mkdir()
    with pytest.raises(NativeCheckpointRecoveryError, match="not a complete native checkpoint"):
        publish_native_checkpoint(storage, job_id="recover-job", step=50, native_checkpoint=incomplete)

    wrong = _native_checkpoint(tmp_path / "wrong", step=51)
    with pytest.raises(NativeCheckpointRecoveryError, match="must be named"):
        publish_native_checkpoint(storage, job_id="recover-job", step=50, native_checkpoint=wrong)
