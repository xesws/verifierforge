from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
HEAVY_ROOTS = {"torch", "vllm", "verl", "ray", "transformers"}


def test_hosted_requirements_exclude_training_and_test_stacks() -> None:
    requirements = (ROOT / "requirements-api.txt").read_text(encoding="utf-8").lower()
    for package in (*HEAVY_ROOTS, "moto", "pytest"):
        assert package not in requirements


def test_reviewer_import_loads_no_training_modules() -> None:
    code = (
        "import json,sys; import app.reviewer.main; "
        "print(json.dumps(sorted({name.split('.')[0] for name in sys.modules} "
        "& {'torch','vllm','verl','ray','transformers'})))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={**os.environ, "VF_REVIEW_INVITE_CODE": "import-only-fixture"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == []


def test_hosted_shell_and_railway_single_service_contract() -> None:
    subprocess.run(
        ["bash", "-n", "scripts/start_hosted_backend.sh"], cwd=ROOT, check=True
    )
    railway = json.loads((ROOT / "railway.json").read_text(encoding="utf-8"))
    assert railway["build"]["dockerfilePath"] == "Dockerfile"
    assert railway["deploy"]["numReplicas"] == 1
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER verifierforge" in dockerfile
    assert "requirements-api.txt" in dockerfile
