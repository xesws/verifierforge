from __future__ import annotations

import os
from io import BytesIO
from types import SimpleNamespace
import subprocess

import pytest

from app.sandbox import DockerSandbox, SandboxUnavailableError


class FakeProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        times_out: bool = False,
    ) -> None:
        self.returncode = returncode
        self.stdout = BytesIO(stdout)
        self.stderr = BytesIO(stderr)
        self.times_out = times_out
        self.killed = False
        self.wait_timeouts: list[float | None] = []

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if self.times_out and not self.killed:
            raise subprocess.TimeoutExpired("docker run", timeout)
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_safe_candidate_uses_constrained_docker_command(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    process = FakeProcess(stdout=b"verified\n")

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return process

    monkeypatch.setattr("app.sandbox.docker.subprocess.Popen", fake_popen)
    result = DockerSandbox().validate("print('verified')")

    command, kwargs = calls[0]
    assert result.passed is True
    assert result.stdout == "verified\n"
    assert result.timed_out is False
    assert command[:3] == ["docker", "run", "--rm"]
    assert "--network=none" in command
    assert "--read-only" in command
    assert command[command.index("--user") + 1] == "65534:65534"
    assert "--cap-drop=ALL" in command
    assert command[command.index("--security-opt") + 1] == "no-new-privileges"
    assert command[command.index("--pids-limit") + 1] == "64"
    assert command[command.index("--cpus") + 1] == "1"
    assert command[command.index("--memory") + 1] == "512m"
    assert command[command.index("--tmpfs") + 1] == "/tmp:rw,noexec,nosuid,size=64m"
    volume = command[command.index("--volume") + 1]
    assert volume.endswith(":/input:ro")
    assert command[-2:] == ["verifierforge-sandbox:latest", "/input/candidate.py"]
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.PIPE
    assert process.wait_timeouts == [5.0]


@pytest.mark.parametrize(
    ("candidate", "stderr"),
    [
        ("import socket; socket.socket()", "network is unreachable"),
        ("open('/input/changed', 'w')", "Read-only file system"),
    ],
)
def test_candidate_failures_are_not_retried_on_host(monkeypatch, candidate, stderr) -> None:
    calls: list[list[str]] = []

    def fake_popen(command, **kwargs):
        calls.append(command)
        return FakeProcess(returncode=1, stderr=stderr.encode())

    monkeypatch.setattr("app.sandbox.docker.subprocess.Popen", fake_popen)
    result = DockerSandbox().validate(candidate)

    assert result.passed is False
    assert result.stderr == stderr
    assert calls == [calls[0]]
    assert calls[0][0:2] == ["docker", "run"]


def test_timeout_kills_named_container_and_returns_bounded_diagnostics(monkeypatch) -> None:
    calls: list[list[str]] = []
    process = FakeProcess(stdout=b"x" * 40000, times_out=True)

    def fake_popen(command, **kwargs):
        calls.append(command)
        return process

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("app.sandbox.docker.subprocess.Popen", fake_popen)
    monkeypatch.setattr("app.sandbox.docker.subprocess.run", fake_run)
    result = DockerSandbox().validate("while True: pass")

    assert result.passed is False
    assert result.timed_out is True
    assert result.returncode is None
    assert "timed out after 5 seconds" in result.stderr
    assert len(result.stdout.encode()) + len(result.stderr.encode()) <= 32 * 1024
    assert calls[1][:2] == ["docker", "kill"]
    assert calls[1][2] == calls[0][calls[0].index("--name") + 1]


def test_missing_docker_surfaces_an_error_without_host_fallback(monkeypatch) -> None:
    def missing_docker(command, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr("app.sandbox.docker.subprocess.Popen", missing_docker)

    with pytest.raises(SandboxUnavailableError, match="Docker executable is unavailable"):
        DockerSandbox().validate("print('never run on the host')")


def test_daemon_failure_surfaces_an_error(monkeypatch) -> None:
    def unavailable_daemon(command, **kwargs):
        return FakeProcess(
            returncode=125,
            stderr=b"Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
        )

    monkeypatch.setattr("app.sandbox.docker.subprocess.Popen", unavailable_daemon)

    with pytest.raises(SandboxUnavailableError, match="Cannot connect to the Docker daemon"):
        DockerSandbox().validate("print('never run on the host')")


@pytest.mark.skipif(
    os.environ.get("VF_RUN_DOCKER_INTEGRATION") != "1",
    reason="set VF_RUN_DOCKER_INTEGRATION=1 to run the Docker integration check",
)
def test_docker_integration_safe_candidate() -> None:
    """Run only when Docker Desktop is available and explicitly enabled."""
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        pytest.skip("Docker Desktop is not available")

    result = DockerSandbox().validate("print('safe')")
    assert result.passed is True
    assert result.stdout == "safe\n"
