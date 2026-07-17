"""Pass S3 credentials into one detached tmux job without writing a secret file."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping


_JOB = re.compile(r"[A-Za-z0-9._-]+\Z")
_CONFIG = re.compile(r"[A-Za-z0-9._/@+=:-]+\Z")
_REQUIRED = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION", "VF_S3_BUCKET")
_OPTIONAL = ("AWS_SESSION_TOKEN", "VF_S3_PREFIX", "VF_S3_REGION")


def local_payload(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return only the approved values for the one S3-backed job."""
    values = os.environ if environ is None else environ
    payload: dict[str, str] = {"VF_STORAGE_BACKEND": "s3"}
    for name in (*_REQUIRED, *_OPTIONAL):
        value = values.get(name)
        if name in _REQUIRED and not value:
            raise ValueError(f"missing required S3 runtime variable: {name}")
        if value:
            _validate_value(name, value)
            payload[name] = value
    return payload


def launch_from_stdin(*, root: Path, python: str, job: str, config: str) -> None:
    """Read a private stdin payload and create a session-scoped tmux job."""
    _validate_job_and_config(job, config)
    try:
        raw = json.load(sys.stdin)
    except json.JSONDecodeError as error:
        raise ValueError("S3 credential payload is not valid JSON") from error
    environment = _validate_payload(raw)
    environment["VF_S3_CACHE_DIR"] = f"/tmp/verifierforge-s3-cache/{job}"

    pgid_path = root / "runs" / job / "pgid"
    lifecycle_path = root / "runs" / job / "evidence" / "s3-credential-lifecycle.json"
    log_path = root / "runs" / job / "train.log"
    pgid_path.parent.mkdir(parents=True, exist_ok=True)
    pgid_path.unlink(missing_ok=True)
    lifecycle_path.parent.mkdir(parents=True, exist_ok=True)

    if _session_exists(job):
        raise RuntimeError(f"tmux session already exists for job {job!r}")
    command = _job_shell_command(root, job, config, pgid_path, lifecycle_path, log_path)
    arguments = ["tmux", "new-session", "-d", "-s", job]
    for name in sorted(environment):
        arguments.extend(("-e", f"{name}={environment[name]}"))
    arguments.append(command)
    completed = subprocess.run(arguments, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if completed.returncode:
        raise RuntimeError("tmux could not start the S3-backed job")

    for _ in range(20):
        if pgid_path.is_file() and _valid_pgid(pgid_path.read_text(encoding="utf-8")):
            print(json.dumps({"status": "started", "job_id": job, "storage": "s3"}))
            return
        time.sleep(0.1)
    subprocess.run(["tmux", "kill-session", "-t", job], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    raise RuntimeError("S3-backed tmux job did not publish a PGID marker")


def _validate_payload(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("S3 credential payload must be a JSON object")
    allowed = {"VF_STORAGE_BACKEND", *_REQUIRED, *_OPTIONAL}
    if set(raw) - allowed:
        raise ValueError("S3 credential payload contains unsupported keys")
    payload: dict[str, str] = {}
    for name in _REQUIRED:
        value = raw.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"missing required S3 runtime variable: {name}")
        _validate_value(name, value)
        payload[name] = value
    for name in _OPTIONAL:
        value = raw.get(name)
        if value is None:
            continue
        if not isinstance(value, str) or not value:
            raise ValueError(f"invalid optional S3 runtime variable: {name}")
        _validate_value(name, value)
        payload[name] = value
    if raw.get("VF_STORAGE_BACKEND") != "s3":
        raise ValueError("S3 credential payload must select VF_STORAGE_BACKEND=s3")
    payload["VF_STORAGE_BACKEND"] = "s3"
    return payload


def _job_shell_command(
    root: Path,
    job: str,
    config: str,
    pgid_path: Path,
    lifecycle_path: Path,
    log_path: Path,
) -> str:
    """Build shell text without embedding any secret value."""
    cleanup = " ".join((*_REQUIRED, *_OPTIONAL, "VF_STORAGE_BACKEND", "VF_S3_CACHE_DIR"))
    inner = "\n".join(
        (
            "set -euo pipefail",
            f"printf '%s\\n' \"$$\" > {shlex.quote(str(pgid_path))}",
            f"mkdir -p {shlex.quote(str(lifecycle_path.parent))}",
            f"printf '%s\\n' '{{\"storage_credentials\":\"injected\"}}' > {shlex.quote(str(lifecycle_path))}",
            "cleanup() {",
            f"  unset {cleanup}",
            f"  printf '%s\\n' '{{\"storage_credentials\":\"cleared\"}}' > {shlex.quote(str(lifecycle_path))}",
            "}",
            "trap cleanup EXIT",
            "trap 'exit 143' HUP INT TERM",
            f"bash trainer/launch.sh {shlex.quote(job)} {shlex.quote(config)}",
        )
    )
    return (
        f"cd {shlex.quote(str(root))} && "
        f"exec setsid bash -c {shlex.quote(inner)} 2>&1 | tee {shlex.quote(str(log_path))}"
    )


def _session_exists(job: str) -> bool:
    return subprocess.run(["tmux", "has-session", "-t", job], check=False).returncode == 0


def _valid_pgid(value: str) -> bool:
    return bool(re.fullmatch(r"[1-9][0-9]*\s*", value))


def _validate_job_and_config(job: str, config: str) -> None:
    if not _JOB.fullmatch(job):
        raise ValueError("invalid job id")
    if not _CONFIG.fullmatch(config):
        raise ValueError("invalid trainer config")


def _validate_value(name: str, value: str) -> None:
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError(f"invalid S3 runtime value: {name}")


def _emit_payload() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError as error:  # pragma: no cover - local dependency boundary.
        raise RuntimeError("S3 payload emission requires python-dotenv") from error
    load_dotenv()
    json.dump(local_payload(), sys.stdout, separators=(",", ":"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely launch one S3-backed tmux job")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--emit-payload", action="store_true")
    mode.add_argument("--launch", action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--python")
    parser.add_argument("--job")
    parser.add_argument("--config")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.emit_payload:
        _emit_payload()
        return
    if not all((args.root, args.python, args.job, args.config)):
        raise SystemExit("--launch requires --root, --python, --job, and --config")
    launch_from_stdin(root=args.root, python=args.python, job=args.job, config=args.config)


if __name__ == "__main__":
    main()
