from __future__ import annotations

import json
from pathlib import Path

import boto3
from moto import mock_aws
import pytest

from app.provisioning.live import S3RunCollector, validate_p2_config


def _config(**overrides):
    value = {
        "base_model": "Qwen/Qwen2.5-0.5B-Instruct",
        "steps": 100,
        "k": 8,
        "checkpoint_interval": 50,
        "budget_usd_cap": 5,
        "provider_pref": "runpod",
    }
    value.update(overrides)
    return value


def _put(client, key: str, body: bytes) -> dict[str, object]:
    import hashlib

    client.put_object(Bucket="p2-test", Key=key, Body=body)
    return {"path": Path(key).name, "key": key, "size_bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def test_p2_config_accepts_only_the_approved_execution_profile() -> None:
    assert validate_p2_config(_config()).steps == 100
    for bad in (
        {"steps": 101},
        {"k": 4},
        {"base_model": "Qwen/Qwen2.5-1.5B-Instruct"},
        {"provider_pref": "auto"},
        {"budget_usd_cap": 5.01},
    ):
        with pytest.raises(ValueError):
            validate_p2_config(_config(**bad))


def test_s3_collector_requires_and_hashes_complete_step_100(tmp_path: Path) -> None:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="p2-test")
        root = "vf/jobs/p2-job"
        collector = S3RunCollector(client, bucket="p2-test", prefix="vf", job_id="p2-job")
        client.put_object(
            Bucket="p2-test",
            Key=f"{root}/metrics.jsonl/000000000099-a.json",
            Body=b'{"step":99}\n',
        )
        assert collector.snapshot().complete is False

        client.put_object(
            Bucket="p2-test",
            Key=f"{root}/metrics.jsonl/000000000100-b.json",
            Body=b'{"step":100}\n',
        )
        checkpoint = _put(client, f"{root}/ckpt/.tmp/step_100/generation/state.bin", b"checkpoint")
        client.put_object(
            Bucket="p2-test",
            Key=f"{root}/ckpt/step_100/manifest.json",
            Body=json.dumps({"step": 100, "files": [checkpoint]}).encode(),
        )
        for name, body in (("final/model.txt", b"model"), ("curve.png", b"png")):
            payload = _put(client, f"{root}/artifacts/.tmp/generation/{name}", body)
            client.put_object(
                Bucket="p2-test",
                Key=f"{root}/artifacts/{name}.manifest.json",
                Body=json.dumps({"files": [payload]}).encode(),
            )

        assert collector.snapshot().complete is True
        inventory = collector.collect(tmp_path / "collected")
        assert inventory["snapshot"]["latest_step"] == 100
        assert (tmp_path / "collected" / "model.txt").read_bytes() == b"model"
        assert (tmp_path / "collected" / "curve.png").read_bytes() == b"png"
        assert len(inventory["objects"]) == 8


def test_s3_collector_surfaces_checkpoint_publication_failure() -> None:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="p2-test")
        root = "vf/jobs/p2-failed"
        client.put_object(
            Bucket="p2-test",
            Key=f"{root}/artifacts/checkpoint-publication-failure.json.manifest.json",
            Body=b"{}",
        )

        snapshot = S3RunCollector(
            client,
            bucket="p2-test",
            prefix="vf",
            job_id="p2-failed",
        ).snapshot()

        assert snapshot.failure_ready is True
        assert snapshot.complete is False
