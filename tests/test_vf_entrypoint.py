from __future__ import annotations

from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_scripts_gate_a_is_importable_from_repository_root() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "import scripts.gate_a"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_vf_train_preflights_gate_a_import_before_tmux_detach() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "vf").read_text(encoding="utf-8")
    import_command = '"$python" -c \'import scripts.gate_a\''

    assert import_command in script
    assert script.index(import_command) < script.index('tmux new-session -d -s "$job"')


def test_vf_s3_train_uses_stdin_payload_helper_without_secret_shell_expansion() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "vf").read_text(encoding="utf-8")

    assert '[[ "${VF_STORAGE_BACKEND:-local}" == "s3" ]]' in script
    assert "start_remote_s3_job" in script
    assert "python3 -m scripts.s3_job_env --emit-payload |" in script
    assert "scripts.s3_job_env --launch" in script


def test_vf_kill_owns_recorded_job_process_groups_and_gpu_cleanup() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "vf").read_text(encoding="utf-8")

    assert "setsid bash -c" in script
    assert "runs/$job/pgid" in script
    assert 'kill -TERM -- "-$pgid"' in script
    assert 'kill -KILL -- "-$pgid"' in script
    assert 'ray" stop --force' in script
    assert "nvidia-smi --query-compute-apps=pid,process_name,used_memory" in script
    assert "vf kill failed: GPU still has" in script
    assert '"storage_credentials":"cleared","cleared_by":"vf_kill"' in script
    assert 'lifecycle="runs/$job/evidence/s3-credential-lifecycle.json"' in script


def test_vf_watch_excludes_pod_only_failed_staging_weights() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "vf").read_text(encoding="utf-8")

    assert "--exclude='evidence/failed-staging/'" in script


def test_vf_bootstrap_waits_for_a_durable_runtime_install_status() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "vf").read_text(encoding="utf-8")

    assert 'session="vf-bootstrap"' in script
    assert 'status_file="$provisioning_dir/runtime-install.status"' in script
    assert 'log_file="$provisioning_dir/runtime-install.log"' in script
    assert 'while tmux has-session -t "$session"' in script
    assert "grep -qx 'state=success'" in script
    assert "vf bootstrap failed: runtime installation did not complete successfully" in script


def test_bootstrap_publishes_log_and_exit_status_without_masking_pip_failure() -> None:
    script = (REPOSITORY_ROOT / "trainer" / "bootstrap.sh").read_text(encoding="utf-8")

    assert "set -euo pipefail" in script
    assert 'RUNTIME_INSTALL_LOG="$PROVISIONING_DIR/runtime-install.log"' in script
    assert 'RUNTIME_INSTALL_STATUS="$PROVISIONING_DIR/runtime-install.status"' in script
    assert "trap publish_runtime_status EXIT" in script
    assert 'exec > >(tee -a "$RUNTIME_INSTALL_LOG") 2>&1' in script
    assert ".venv/bin/python -m pip install -r requirements-trainer.txt" in script


def test_blackwell_smoke_is_an_explicit_launch_target() -> None:
    launch = (REPOSITORY_ROOT / "trainer" / "launch.sh").read_text(encoding="utf-8")

    assert "grpo_v1_1p5b_blackwell_smoke)" in launch
    assert "grpo_v1_1p5b_h100_smoke)" in launch
    assert 'trainer.grpo_train --job "$job" --config "$cfg"' in launch
    assert "h100_diagnostic_environment()" in launch
    assert "export HF_HUB_OFFLINE=1" in launch
    assert "export TRANSFORMERS_OFFLINE=1" in launch
    assert "export VLLM_LOGGING_LEVEL=INFO" in launch
    assert "export RAY_DEDUP_LOGS=0" in launch
    assert "export PYTHONFAULTHANDLER=1" in launch
