"""Run untrusted verifier candidates inside a deliberately constrained Docker container."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from typing import BinaryIO, Final
from uuid import uuid4


DEFAULT_IMAGE: Final = "verifierforge-sandbox:latest"
DEFAULT_TIMEOUT_SECONDS: Final = 5.0
DEFAULT_OUTPUT_LIMIT_BYTES: Final = 32 * 1024
_CLEANUP_TIMEOUT_SECONDS: Final = 1.0
_TRUNCATION_MARKER: Final = "\n...[truncated]"


class SandboxUnavailableError(RuntimeError):
    """Raised when Docker is unavailable, rather than executing code on the host."""


@dataclass(frozen=True)
class SandboxResult:
    """Bounded diagnostics from one candidate validation attempt."""

    passed: bool
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    returncode: int | None


class _BoundedCapture:
    """Drain a subprocess stream without retaining more than its byte budget."""

    def __init__(self, byte_limit: int) -> None:
        self._byte_limit = byte_limit
        self._marker = _TRUNCATION_MARKER.encode()[:byte_limit]
        self._payload_limit = max(0, byte_limit - len(self._marker))
        self._data = bytearray()
        self._truncated = False
        self._lock = threading.Lock()

    def append(self, chunk: bytes) -> None:
        with self._lock:
            remaining = self._payload_limit - len(self._data)
            if remaining > 0:
                self._data.extend(chunk[:remaining])
            if len(chunk) > remaining:
                self._truncated = True

    def text(self) -> str:
        with self._lock:
            text = bytes(self._data).decode("utf-8", errors="replace")
            return text + self._marker.decode("utf-8", errors="ignore") if self._truncated else text


class DockerSandbox:
    """Validate one Python candidate without a host-process fallback.

    Build the image before use with:
    ``docker build -t verifierforge-sandbox:latest app/sandbox``.
    """

    def __init__(
        self,
        *,
        image: str = DEFAULT_IMAGE,
        docker_binary: str = "docker",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if output_limit_bytes <= 0:
            raise ValueError("output_limit_bytes must be positive")
        self.image = image
        self.docker_binary = docker_binary
        self.timeout_seconds = timeout_seconds
        self.output_limit_bytes = output_limit_bytes

    def validate(self, candidate_code: str) -> SandboxResult:
        """Execute candidate source in Docker and return bounded diagnostics.

        The source is mounted read-only at ``/input/candidate.py``. A nonzero
        exit status is a failed validation; a missing Docker CLI or daemon is
        surfaced as :class:`SandboxUnavailableError` rather than falling back
        to host Python.
        """
        if not candidate_code.strip():
            raise ValueError("candidate_code must not be empty")

        container_name = f"vf-sandbox-{uuid4().hex}"
        with tempfile.TemporaryDirectory(prefix="vf-sandbox-") as temporary_dir:
            input_dir = Path(temporary_dir)
            # TemporaryDirectory defaults to 0700; the fixed non-root container
            # uid must be able to traverse this otherwise read-only mount.
            input_dir.chmod(0o755)
            candidate_path = input_dir / "candidate.py"
            candidate_path.write_text(candidate_code, encoding="utf-8")
            candidate_path.chmod(0o444)
            command = self._command(input_dir, container_name)
            started = time.monotonic()
            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except FileNotFoundError as error:
                raise SandboxUnavailableError(
                    f"Docker executable is unavailable: {self.docker_binary}"
                ) from error

            stdout, stderr, returncode, timed_out = self._collect_output(
                process, container_name
            )

        if self._docker_daemon_unavailable(returncode, stderr):
            raise SandboxUnavailableError(stderr or "Docker daemon is unavailable.")
        return SandboxResult(
            passed=returncode == 0 and not timed_out,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=time.monotonic() - started,
            timed_out=timed_out,
            returncode=None if timed_out else returncode,
        )

    def _collect_output(
        self, process: subprocess.Popen[bytes], container_name: str
    ) -> tuple[str, str, int, bool]:
        """Drain both pipes concurrently, enforcing the host timeout and byte cap."""
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_limit = self.output_limit_bytes // 2
        stderr_limit = self.output_limit_bytes - stdout_limit
        stdout_capture = _BoundedCapture(stdout_limit)
        stderr_capture = _BoundedCapture(stderr_limit)
        readers = [
            threading.Thread(
                target=self._drain_stream, args=(process.stdout, stdout_capture), daemon=True
            ),
            threading.Thread(
                target=self._drain_stream, args=(process.stderr, stderr_capture), daemon=True
            ),
        ]
        for reader in readers:
            reader.start()

        timed_out = False
        try:
            returncode = process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._kill_container(container_name)
            try:
                process.kill()
            except ProcessLookupError:
                pass
            returncode = process.wait()
        finally:
            for reader in readers:
                reader.join()

        stdout = stdout_capture.text()
        stderr = stderr_capture.text()
        if timed_out:
            timeout_note = f"Sandbox timed out after {self.timeout_seconds:g} seconds."
            stderr = self._append_diagnostic(stderr, timeout_note)
        return stdout, stderr, returncode, timed_out

    @staticmethod
    def _drain_stream(stream: BinaryIO, capture: _BoundedCapture) -> None:
        while chunk := stream.read(8192):
            capture.append(chunk)

    def _command(self, input_dir: Path, container_name: str) -> list[str]:
        """Return the fixed Docker invocation used for every candidate."""
        return [
            self.docker_binary,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network=none",
            "--read-only",
            "--user",
            "65534:65534",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--cpus",
            "1",
            "--memory",
            "512m",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--volume",
            f"{input_dir.resolve()}:/input:ro",
            "--workdir",
            "/tmp",
            self.image,
            "/input/candidate.py",
        ]

    def _kill_container(self, container_name: str) -> None:
        """Best-effort cleanup so a timed-out Docker client cannot leave code running."""
        try:
            subprocess.run(
                [self.docker_binary, "kill", container_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_CLEANUP_TIMEOUT_SECONDS,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            # The original timeout is the useful validation result. There is no
            # safe host-side alternative if Docker cleanup itself is unavailable.
            pass

    def _append_diagnostic(self, existing: str, note: str) -> str:
        """Add a timeout note without exceeding the stderr half of the output cap."""
        stderr_limit = self.output_limit_bytes - (self.output_limit_bytes // 2)
        suffix = self._truncate_text(("\n" if existing else "") + note, stderr_limit)
        available = max(0, stderr_limit - len(suffix.encode("utf-8", errors="replace")))
        return self._truncate_text(existing, available) + suffix

    @staticmethod
    def _truncate_text(value: str, maximum_bytes: int) -> str:
        encoded = value.encode("utf-8", errors="replace")
        if len(encoded) <= maximum_bytes:
            return value
        marker = _TRUNCATION_MARKER.encode("utf-8")
        if maximum_bytes <= len(marker):
            return encoded[:maximum_bytes].decode("utf-8", errors="ignore")
        prefix = encoded[: maximum_bytes - len(marker)].decode("utf-8", errors="ignore")
        return prefix + _TRUNCATION_MARKER

    @staticmethod
    def _docker_daemon_unavailable(returncode: int, stderr: str) -> bool:
        return returncode == 125 and "Cannot connect to the Docker daemon" in stderr
