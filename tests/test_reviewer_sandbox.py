from __future__ import annotations

import json
import base64
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "start_reviewer_sandbox.sh"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _get_json(url: str) -> object:
    with urlopen(url, timeout=1) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_json(url: str) -> object:
    error: Exception | None = None
    for _ in range(80):
        try:
            return _get_json(url)
        except Exception as caught:  # Startup is intentionally asynchronous.
            error = caught
            time.sleep(0.1)
    raise AssertionError(f"sandbox did not become ready: {error}")


def test_launcher_has_valid_shell_syntax() -> None:
    subprocess.run(["bash", "-n", str(LAUNCHER)], cwd=ROOT, check=True)


def test_launcher_serves_artifacts_and_fake_proxy(tmp_path: Path) -> None:
    api_port, proxy_port = _free_port(), _free_port()
    while proxy_port == api_port:
        proxy_port = _free_port()
    environment = {
        **os.environ,
        "PYTHON": sys.executable,
        "VF_REVIEW_RUNTIME_DIR": str(tmp_path / "runtime"),
    }
    process = subprocess.Popen(
        ["bash", str(LAUNCHER), "--api-port", str(api_port), "--proxy-port", str(proxy_port)],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        jobs = _wait_for_json(f"http://127.0.0.1:{api_port}/jobs")
        assert {entry["job_id"] for entry in jobs} == {
            "d4-m3-1p5b-r1-v0125",
            "d4-m4-0p5b-random-v0126",
        }

        payload = json.dumps(
            {
                "model": "vf-demo",
                "messages": [
                    {"role": "system", "content": "Extract support fields."},
                    {"role": "user", "content": "Customer asks for a refund on order 42."},
                ],
            }
        ).encode("utf-8")
        request = Request(
            f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            completion = json.loads(response.read().decode("utf-8"))
        assert completion["object"] == "chat.completion"
        assert completion["choices"][0]["message"]["content"].startswith("vf-fake-completion-")
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=8)


def test_launcher_refuses_one_port_for_both_services() -> None:
    result = subprocess.run(
        ["bash", str(LAUNCHER), "--api-port", "8012", "--proxy-port", "8012"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "ports must differ" in result.stderr


def test_full_mode_composes_authenticated_surface_without_cloud_calls(
    tmp_path: Path,
) -> None:
    public_port = _free_port()
    runtime = tmp_path / "full-runtime"
    environment = {
        **os.environ,
        "PYTHON": sys.executable,
        "VF_REVIEW_RUNTIME_DIR": str(runtime),
        "VF_REVIEW_TEST_MODE": "true",
    }
    process = subprocess.Popen(
        [
            "bash",
            str(LAUNCHER),
            "--mode",
            "full",
            "--public-port",
            str(public_port),
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        health = _wait_for_json(f"http://127.0.0.1:{public_port}/healthz")
        assert health["status"] == "ok"
        assert health["tuned_upstream_reachable"] is None
        for _ in range(80):
            invite_path = runtime / "invite-code.txt"
            if invite_path.exists():
                break
            time.sleep(0.1)
        invite = invite_path.read_text(encoding="utf-8").strip()
        token = base64.b64encode(f"judge:{invite}".encode()).decode()
        request = Request(
            f"http://127.0.0.1:{public_port}/jobs",
            headers={"Authorization": f"Basic {token}"},
        )
        with urlopen(request, timeout=3) as response:
            jobs = json.loads(response.read().decode())
        assert len(jobs) == 2
        public_url_path = runtime / "public-url.txt"
        for _ in range(80):
            if public_url_path.exists():
                break
            time.sleep(0.1)
        assert public_url_path.read_text().strip() == (
            f"http://127.0.0.1:{public_port}"
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=8)
