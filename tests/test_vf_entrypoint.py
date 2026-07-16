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


def test_vf_kill_owns_recorded_job_process_groups_and_gpu_cleanup() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "vf").read_text(encoding="utf-8")

    assert "setsid bash -c" in script
    assert "runs/$job/pgid" in script
    assert 'kill -TERM -- "-$pgid"' in script
    assert 'kill -KILL -- "-$pgid"' in script
    assert 'ray" stop --force' in script
    assert "nvidia-smi --query-compute-apps=pid,process_name,used_memory" in script
    assert "vf kill failed: GPU still has" in script


def test_blackwell_smoke_is_an_explicit_launch_target() -> None:
    launch = (REPOSITORY_ROOT / "trainer" / "launch.sh").read_text(encoding="utf-8")

    assert "grpo_v1_1p5b_blackwell_smoke)" in launch
    assert 'trainer.grpo_train --job "$job" --config "$cfg"' in launch
