"""Self-contained pod bootstrap delivered without persistent cloud credentials."""

from __future__ import annotations

import base64


BOOTSTRAP_SOURCE = r'''from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MODEL_ROOT = Path("/models/vf-demo")
BUFFER = 8 * 1024 * 1024
DIAGNOSTIC_LIMIT = 8000

def fetch(url, destination, expected_size, expected_sha):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    digest = hashlib.sha256()
    size = 0
    with urlopen(url, timeout=120) as response, temporary.open("wb") as output:
        while True:
            block = response.read(BUFFER)
            if not block:
                break
            output.write(block)
            digest.update(block)
            size += len(block)
    if (expected_size >= 0 and size != expected_size) or digest.hexdigest() != expected_sha:
        raise RuntimeError("model object identity mismatch")
    os.replace(temporary, destination)

def publish(payload):
    request = Request(
        os.environ["VF_TUNNEL_CALLBACK_URL"],
        data=json.dumps(payload, sort_keys=True).encode(),
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=60) as response:
        if response.status not in {200, 201}:
            raise RuntimeError("serving callback upload failed")

def redacted_tail(path):
    value = path.read_text(errors="replace")[-DIAGNOSTIC_LIMIT:]
    for name in (
        "VF_ENDPOINT_API_KEY",
        "VF_MODEL_MANIFEST_URL",
        "VF_TUNNEL_CALLBACK_URL",
    ):
        secret = os.environ.get(name, "")
        if secret:
            value = value.replace(secret, "<redacted>")
    return value

def main():
    if os.environ.get("VF_INSTALL_VLLM", "false").lower() == "true":
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir", "vllm==0.10.2"]
        )
    with urlopen(os.environ["VF_MODEL_MANIFEST_URL"], timeout=60) as response:
        manifest = json.load(response)
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError("model manifest has no files")
    identities = []
    for entry in files:
        relative = PurePosixPath(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
            raise RuntimeError("model manifest path is unsafe")
        fetch(
            entry["url"],
            MODEL_ROOT.joinpath(*relative.parts),
            int(entry["size_bytes"]),
            entry["sha256"],
        )
        identities.append((relative.as_posix(), entry["sha256"]))
    tree = hashlib.sha256()
    for relative, sha in sorted(identities):
        tree.update(relative.encode())
        tree.update(b"\0")
        tree.update(sha.encode("ascii"))
        tree.update(b"\n")
    if tree.hexdigest() != manifest["tree_sha256"]:
        raise RuntimeError("model tree identity mismatch")

    cloudflared = Path("/tmp/cloudflared")
    fetch(
        os.environ["VF_CLOUDFLARED_URL"],
        cloudflared,
        int(os.environ["VF_CLOUDFLARED_SIZE"]),
        os.environ["VF_CLOUDFLARED_SHA256"],
    )
    cloudflared.chmod(0o700)
    log = Path("/tmp/cloudflared.log")
    tunnel = subprocess.Popen(
        [str(cloudflared), "tunnel", "--url", "http://127.0.0.1:8000",
         "--no-autoupdate", "--loglevel", "info", "--logfile", str(log)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    public_url = None
    pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and tunnel.poll() is None:
        if log.exists():
            match = pattern.search(log.read_text(errors="replace"))
            if match:
                public_url = match.group(0)
                break
        time.sleep(1)
    if public_url is None:
        raise RuntimeError("cloudflared did not publish a tunnel URL")
    identity = {"url": public_url, "tree_sha256": tree.hexdigest()}
    publish({**identity, "phase": "vllm_starting"})
    command = [
        sys.executable, "-m", "vllm.entrypoints.openai.api_server",
        "--model", str(MODEL_ROOT), "--served-model-name", os.environ["VF_SERVED_MODEL"],
        "--dtype", "bfloat16", "--max-model-len", "2048", "--host", "0.0.0.0",
        "--port", "8000", "--api-key", os.environ["VF_ENDPOINT_API_KEY"],
        "--uvicorn-log-level", "warning",
    ]
    vllm_log = Path("/tmp/vllm.log")
    with vllm_log.open("ab", buffering=0) as output:
        server = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
    headers = {"Authorization": "Bearer " + os.environ["VF_ENDPOINT_API_KEY"]}
    while server.poll() is None:
        try:
            request = Request("http://127.0.0.1:8000/v1/models", headers=headers)
            with urlopen(request, timeout=5) as response:
                if response.status == 200:
                    publish({**identity, "phase": "ready"})
                    return_code = server.wait()
                    publish(
                        {
                            **identity,
                            "phase": "failed",
                            "return_code": return_code,
                            "diagnostic": redacted_tail(vllm_log),
                        }
                    )
                    return
        except (HTTPError, URLError, TimeoutError):
            pass
        time.sleep(5)
    publish(
        {
            **identity,
            "phase": "failed",
            "return_code": server.returncode,
            "diagnostic": redacted_tail(vllm_log),
        }
    )

main()
'''


BOOTSTRAP_B64 = base64.b64encode(BOOTSTRAP_SOURCE.encode("utf-8")).decode("ascii")
BOOTSTRAP_LOADER = (
    "import base64,os;"
    "exec(compile(base64.b64decode(os.environ['VF_BOOTSTRAP_B64']),"
    "'<vf-serving-bootstrap>','exec'))"
)


__all__ = ["BOOTSTRAP_B64", "BOOTSTRAP_LOADER", "BOOTSTRAP_SOURCE"]
