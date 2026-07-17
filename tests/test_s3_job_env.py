import json
from pathlib import Path

import pytest

from scripts.s3_job_env import _job_shell_command, _recovery_shell_command, _validate_payload, local_payload


def _payload() -> dict[str, str]:
    return {
        "VF_STORAGE_BACKEND": "s3",
        "AWS_ACCESS_KEY_ID": "access-id",
        "AWS_SECRET_ACCESS_KEY": "secret-value",
        "AWS_DEFAULT_REGION": "us-east-1",
        "VF_S3_BUCKET": "vf-proof-bucket",
    }


def test_payload_requires_exact_nonempty_approved_values():
    assert _validate_payload(_payload())["VF_STORAGE_BACKEND"] == "s3"
    with pytest.raises(ValueError, match="missing required"):
        _validate_payload({"VF_STORAGE_BACKEND": "s3"})
    with pytest.raises(ValueError, match="unsupported keys"):
        _validate_payload({**_payload(), "UNEXPECTED": "value"})


def test_local_payload_does_not_include_unapproved_environment_values():
    payload = local_payload({**_payload(), "UNRELATED_SECRET": "do-not-copy"})

    assert payload == _payload()
    assert "UNRELATED_SECRET" not in json.dumps(payload)


def test_tmux_shell_command_records_lifecycle_without_embedding_secret_values(tmp_path):
    command = _job_shell_command(
        tmp_path,
        "s3-proof",
        "grpo_v1_0p5b",
        tmp_path / "runs" / "s3-proof" / "pgid",
        tmp_path / "runs" / "s3-proof" / "evidence" / "s3-credential-lifecycle.json",
        tmp_path / "runs" / "s3-proof" / "train.log",
    )

    assert "secret-value" not in command
    assert '"storage_credentials":"injected"' in command
    assert '"storage_credentials":"cleared"' in command
    assert "unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY" in command
    assert "setsid bash -c" in command


def test_recovery_tmux_command_is_credential_free_and_clears_environment(tmp_path):
    command = _recovery_shell_command(
        root=tmp_path,
        python="/venv/bin/python",
        job="s3-proof",
        step=50,
        native=tmp_path / "cache" / "global_step_50",
        lifecycle_path=tmp_path / "runs" / "s3-proof" / "evidence" / "recovery.json",
        log_path=tmp_path / "runs" / "s3-proof" / "evidence" / "recovery.log",
        prior_log=tmp_path / "runs" / "s3-proof" / "train.log",
    )

    assert "secret-value" not in command
    assert "scripts.s3_native_recovery" in command
    assert "--prior-log" in command
    assert '"storage_credentials":"injected"' in command
    assert '"storage_credentials":"cleared"' in command
    assert "unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY" in command
