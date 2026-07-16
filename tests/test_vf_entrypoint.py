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
    import_command = "$REMOTE_PYTHON -c 'import scripts.gate_a'"

    assert import_command in script
    assert script.index(import_command) < script.index("tmux new-session -d -s $job")
