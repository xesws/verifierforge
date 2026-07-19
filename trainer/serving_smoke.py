"""Fail-closed loopback vLLM acceptance for checkpoint publication."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from trainer.export_compat import (
    ConversionResult,
    convert_prefixed_full_export,
    serveable_export_path,
    source_export_path,
)


class ServingSmokeError(RuntimeError):
    """A checkpoint did not meet the vLLM serving acceptance contract."""


@dataclass(frozen=True)
class ServingSmokeResult:
    """The completed acceptance evidence for one converted checkpoint."""

    native_checkpoint: Path
    source_export: Path
    serveable_export: Path
    evidence_path: Path
    port: int
    model_name: str
    conversion: ConversionResult


@dataclass(frozen=True)
class ServingEndpointResult:
    """The endpoint-level proof retained by a loopback vLLM smoke."""

    evidence_path: Path
    port: int
    model_name: str


@dataclass
class _ServerHandle:
    """The process and immutable launch details needed for cleanup/evidence."""

    process: Any
    command: list[str]
    log_path: Path
    stop: Callable[[], None] | None = None


ServerLauncher = Callable[[Path, int, str, Path], _ServerHandle]
RequestJson = Callable[[str, Mapping[str, Any] | None], tuple[int, dict[str, Any]]]
SERVING_SMOKE_GPU_MEMORY_UTILIZATION = 0.90


def validate_checkpoint_for_serving(
    native_checkpoint: Path,
    *,
    lora_rank: int,
    lora_alpha: int,
    evidence_path: Path,
    launcher: ServerLauncher | None = None,
    request_json: RequestJson | None = None,
    port_selector: Callable[[], int] | None = None,
) -> ServingSmokeResult:
    """Convert and serve-smoke a staged native checkpoint before Storage sees it.

    The raw verl export remains the native resume source.  Its standard-HF
    sibling is an additional, atomic serving representation.  Conversion
    failure is evidence too and blocks publication before any Storage copy.
    """
    native_checkpoint = Path(native_checkpoint)
    evidence_path = Path(evidence_path)
    source = source_export_path(native_checkpoint)
    destination = serveable_export_path(native_checkpoint)
    try:
        conversion = convert_prefixed_full_export(
            source,
            destination,
            lora_rank=lora_rank,
            lora_alpha=lora_alpha,
        )
    except Exception as error:
        _write_json_atomic(
            evidence_path,
            {
                "schema_version": 1,
                "status": "failed",
                "phase": "conversion",
                "native_checkpoint": str(native_checkpoint),
                "source_export": str(source),
                "serveable_export": str(destination),
                "error": _error_payload(error),
            },
        )
        raise ServingSmokeError(f"checkpoint export conversion failed: {error}") from error

    endpoint = smoke_serveable_export(
        destination,
        evidence_path=evidence_path,
        metadata={
            "native_checkpoint": str(native_checkpoint),
            "source_export": str(source),
            "conversion": conversion.as_dict(),
        },
        launcher=launcher,
        request_json=request_json,
        port_selector=port_selector,
    )
    return ServingSmokeResult(
        native_checkpoint=native_checkpoint,
        source_export=source,
        serveable_export=destination,
        evidence_path=evidence_path,
        port=endpoint.port,
        model_name=endpoint.model_name,
        conversion=conversion,
    )


def smoke_serveable_export(
    export_path: Path,
    *,
    evidence_path: Path,
    metadata: Mapping[str, Any] | None = None,
    launcher: ServerLauncher | None = None,
    request_json: RequestJson | None = None,
    port_selector: Callable[[], int] | None = None,
    readiness_timeout: float = 300.0,
) -> ServingEndpointResult:
    """Require models visibility and a real completion from one local vLLM server."""
    export_path = Path(export_path)
    evidence_path = Path(evidence_path)
    launch = launcher or _launch_vllm
    request = request_json or _request_json
    port = (port_selector or _free_loopback_port)()
    model_name = f"vf-serving-smoke-{export_path.parent.parent.name}"
    handle: _ServerHandle | None = None
    models_status: int | None = None
    models: dict[str, Any] | None = None
    completion_status: int | None = None
    completion: dict[str, Any] | None = None
    error: Exception | None = None

    try:
        handle = launch(export_path, port, model_name, evidence_path)
        models_status, models = _wait_for_models(
            handle,
            port=port,
            request_json=request,
            readiness_timeout=readiness_timeout,
        )
        listed = {item.get("id") for item in models.get("data", []) if isinstance(item, dict)}
        if model_name not in listed:
            raise ServingSmokeError(f"/v1/models did not list {model_name!r}: {listed}")
        completion_request = {
            "model": model_name,
            "prompt": "Return exactly: SELECT 1;",
            "max_tokens": 8,
            "temperature": 0.0,
        }
        completion_status, completion = request(
            f"http://127.0.0.1:{port}/v1/completions", completion_request
        )
        choices = completion.get("choices") if isinstance(completion, dict) else None
        if completion_status != 200 or not isinstance(choices, list) or not choices:
            raise ServingSmokeError("vLLM completion endpoint did not return a non-empty choices list")
    except Exception as caught:
        error = caught
    finally:
        if handle is not None:
            _stop_server(handle)

    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": "completed" if error is None else "failed",
        "phase": "serving",
        "export_path": str(export_path),
        "port": port,
        "model_name": model_name,
        "models_status": models_status,
        "models": models,
        "completion_status": completion_status,
        "completion": completion,
        "metadata": dict(metadata or {}),
    }
    if handle is not None:
        payload["command"] = handle.command
        payload["server_log_path"] = str(handle.log_path)
        payload["server_log_tail"] = _tail(handle.log_path)
    if error is not None:
        payload["error"] = _error_payload(error)
    _write_json_atomic(evidence_path, payload)

    if error is not None:
        raise ServingSmokeError(f"vLLM serving smoke failed: {type(error).__name__}: {error}") from error

    return ServingEndpointResult(
        evidence_path=evidence_path,
        port=port,
        model_name=model_name,
    )


def _launch_vllm(export_path: Path, port: int, model_name: str, evidence_path: Path) -> _ServerHandle:
    executable = Path(sys.executable).with_name("vllm")
    if not executable.is_file():
        raise ServingSmokeError(f"vLLM executable is unavailable: {executable}")
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = evidence_path.with_name(f"{evidence_path.stem}.vllm.log")
    command = _vllm_command(executable, export_path, port, model_name)
    environment = os.environ.copy()
    environment.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "VLLM_LOGGING_LEVEL": "INFO",
            "RAY_DEDUP_LOGS": "0",
            "PYTHONFAULTHANDLER": "1",
        }
    )
    with log_path.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(
            command,
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    return _ServerHandle(process=process, command=command, log_path=log_path)


def _vllm_command(
    executable: Path,
    export_path: Path,
    port: int,
    model_name: str,
) -> list[str]:
    return [
        str(executable),
        "serve",
        str(export_path),
        "--served-model-name",
        model_name,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--dtype",
        "bfloat16",
        "--gpu-memory-utilization",
        f"{SERVING_SMOKE_GPU_MEMORY_UTILIZATION:.2f}",
        "--max-model-len",
        "64",
        "--max-num-seqs",
        "1",
        "--tensor-parallel-size",
        "1",
        "--enforce-eager",
        "--disable-log-stats",
    ]


def _wait_for_models(
    handle: _ServerHandle,
    *,
    port: int,
    request_json: RequestJson,
    readiness_timeout: float,
) -> tuple[int, dict[str, Any]]:
    deadline = time.monotonic() + readiness_timeout
    last_error: Exception | None = None
    url = f"http://127.0.0.1:{port}/v1/models"
    while time.monotonic() < deadline:
        if handle.process.poll() is not None:
            raise ServingSmokeError(f"vLLM exited before readiness: {_tail(handle.log_path)}")
        try:
            status, payload = request_json(url, None)
        except (OSError, URLError, ValueError) as error:
            last_error = error
            time.sleep(1)
            continue
        if status == 200:
            return status, payload
        last_error = ServingSmokeError(f"/v1/models returned HTTP {status}")
        time.sleep(1)
    detail = f"; last request error: {type(last_error).__name__}: {last_error}" if last_error else ""
    raise ServingSmokeError(f"vLLM readiness timeout after {readiness_timeout:.0f}s{detail}")


def _request_json(url: str, payload: Mapping[str, Any] | None) -> tuple[int, dict[str, Any]]:
    request = Request(url, method="GET")
    if payload is not None:
        request = Request(
            url,
            data=json.dumps(dict(payload)).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    with urlopen(request, timeout=15) as response:  # noqa: S310 - loopback URL is constructed above.
        body = json.load(response)
        if not isinstance(body, dict):
            raise ValueError("vLLM response must be a JSON object")
        return response.status, body


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop_server(handle: _ServerHandle) -> None:
    if handle.stop is not None:
        handle.stop()
        return
    if handle.process.poll() is not None:
        return
    try:
        os.killpg(handle.process.pid, signal.SIGTERM)
        handle.process.wait(timeout=20)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(handle.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _tail(path: Path, *, limit: int = 16_000) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return "<log unavailable>"


def _error_payload(error: BaseException) -> dict[str, str]:
    return {"type": type(error).__name__, "message": str(error)[:16_000]}


def _write_json_atomic(path: Path, payload: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
