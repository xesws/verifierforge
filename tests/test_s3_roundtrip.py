import json

import boto3
from moto import mock_aws

from scripts.s3_roundtrip import run


def test_real_roundtrip_helper_uses_manifest_and_fifty_append_only_records(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("VF_S3_BUCKET", "verifierforge-roundtrip-tests")
    monkeypatch.setenv("VF_S3_PREFIX", "roundtrip")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="verifierforge-roundtrip-tests")
        evidence = tmp_path / "roundtrip.json"
        result = run(job_id="s3-proof", metric_count=50, evidence_path=evidence)

    assert result["status"] == "passed"
    assert result["metric_count"] == 50
    assert result["interrupted_manifest_visible"] is False
    assert result["object_count"] >= 53
    assert json.loads(evidence.read_text(encoding="utf-8")) == result
