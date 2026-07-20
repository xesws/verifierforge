"""Provider runtimes for one bounded serving session."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
from typing import Awaitable, Callable, Mapping, Protocol

import httpx

from app.provisioning.product import CredentialResolver
from app.provisioning.errors import ProvisionProviderError
from app.provisioning.runpod import RunPodAdapter, RunPodRuntimeConfig
from app.provisioning.termination import DeletionReceipt, confirm_deleted, schedule_billing
from app.serving.bootstrap import BOOTSTRAP_B64, BOOTSTRAP_LOADER
from app.serving.settings import (
    CLOUDFLARED_SHA256,
    CLOUDFLARED_VERSION,
    ServingSettings,
)
from core.provisioning_contracts import (
    GPUClass,
    ProvisionHandle,
    ProvisionProvider,
    ProvisionSpec,
)


CLOUDFLARED_URL = (
    "https://github.com/cloudflare/cloudflared/releases/download/"
    f"{CLOUDFLARED_VERSION}/cloudflared-linux-amd64"
)
StateCallback = Callable[[ProvisionHandle, float], Awaitable[None]]


class ServingRuntimeError(RuntimeError):
    """Stable failure that excludes credentials and presigned URLs."""


@dataclass(frozen=True)
class ReadyRuntime:
    handle: ProvisionHandle
    url: str
    cost_accrued_usd: float
    cold_start_seconds: float


class ServingRuntime(Protocol):
    async def start(
        self,
        *,
        session_id: str,
        model_id: str,
        endpoint_api_key: str,
        on_allocated: StateCallback,
    ) -> ReadyRuntime: ...

    async def terminate(self, handle: ProvisionHandle) -> DeletionReceipt: ...


@dataclass
class MockServingRuntime:
    delay_seconds: float = 0.0
    starts: int = 0
    terminations: int = 0

    async def start(
        self,
        *,
        session_id: str,
        model_id: str,
        endpoint_api_key: str,
        on_allocated: StateCallback,
    ) -> ReadyRuntime:
        del endpoint_api_key
        self.starts += 1
        handle = ProvisionHandle(
            provider=ProvisionProvider.RUNPOD,
            external_id=f"mock-{session_id[:20]}",
            job_id=f"serve-{session_id[:20]}",
            approval_id=session_id,
            labels={
                "gpu_model": "Mock RTX 2000 Ada",
                "hourly_price_usd": "0.100000",
            },
        )
        await on_allocated(handle, 0.0)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return ReadyRuntime(
            handle=handle,
            url="https://mock-serving.example.test/v1",
            cost_accrued_usd=0.0,
            cold_start_seconds=self.delay_seconds,
        )

    async def terminate(self, handle: ProvisionHandle) -> DeletionReceipt:
        self.terminations += 1
        return DeletionReceipt(
            external_id=handle.external_id,
            checked_at=datetime.now(timezone.utc).isoformat(),
            target_absent=True,
            vf_auto_prefix_count=0,
        )


class RunPodServingRuntime:
    def __init__(
        self,
        *,
        settings: ServingSettings,
        credential_resolver: CredentialResolver,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.settings = settings
        self.credential_resolver = credential_resolver
        self.environ = os.environ if environ is None else environ
        self.bucket = self.environ.get("VF_S3_BUCKET", "").strip()
        self.prefix = self.environ.get("VF_S3_PREFIX", "vf").strip("/")
        self.region = self.environ.get("VF_S3_REGION", "").strip() or None
        if not self.bucket:
            raise ServingRuntimeError("VF_S3_BUCKET is required for live serving")
        self._s3 = _s3_client(self.region)

    async def start(
        self,
        *,
        session_id: str,
        model_id: str,
        endpoint_api_key: str,
        on_allocated: StateCallback,
    ) -> ReadyRuntime:
        prepared = await asyncio.to_thread(self._prepare_download, session_id)
        runtime_env = {
            "VF_BOOTSTRAP_B64": BOOTSTRAP_B64,
            "VF_MODEL_MANIFEST_URL": prepared.manifest_url,
            "VF_TUNNEL_CALLBACK_URL": prepared.callback_put_url,
            "VF_CLOUDFLARED_URL": CLOUDFLARED_URL,
            "VF_CLOUDFLARED_SIZE": "-1",
            "VF_CLOUDFLARED_SHA256": CLOUDFLARED_SHA256,
            "VF_ENDPOINT_API_KEY": endpoint_api_key,
            "VF_SERVED_MODEL": model_id,
            "VF_INSTALL_VLLM": "true" if self.settings.install_vllm else "false",
        }
        runtime = RunPodRuntimeConfig(
            http_ports=(8000,),
            docker_entrypoint=("python", "-c"),
            docker_start_cmd=(BOOTSTRAP_LOADER,),
            ephemeral_env_provider=lambda: runtime_env,
        )
        spec = ProvisionSpec(
            job_id=f"serve-{session_id[:24]}",
            approval_id=session_id,
            requested_by="serving-orchestrator",
            provider=ProvisionProvider.RUNPOD,
            gpu_class=GPUClass.SMALL_ADA,
            image=self.settings.image,
            container_disk_gb=40,
            env={"VF_WORKLOAD": "serving"},
            ports=[8000],
            ssh_pubkey=_ephemeral_public_key(),
            budget_usd_cap=self.settings.budget_usd_cap,
            max_runtime_min=self.settings.max_runtime_min,
        )
        started = asyncio.get_running_loop().time()
        handle: ProvisionHandle | None = None
        adapter = RunPodAdapter(
            api_key_provider=self.credential_resolver,
            runtime=runtime,
            create_timeout_s=30,
        )
        try:
            handle = await adapter.provision(spec)
            try:
                await on_allocated(handle, 0.0)
            except Exception:
                await adapter.terminate(handle)
                await confirm_deleted(adapter, handle, timeout_s=10 * 60, poll_s=15)
                raise ServingRuntimeError(
                    "provider allocation could not be persisted; deletion confirmed"
                ) from None
            url, cost = await self._wait_ready(
                adapter,
                handle,
                session_id=session_id,
                model_id=model_id,
                endpoint_api_key=endpoint_api_key,
                callback_key=prepared.callback_key,
            )
            return ReadyRuntime(
                handle=handle,
                url=url,
                cost_accrued_usd=cost,
                cold_start_seconds=asyncio.get_running_loop().time() - started,
            )
        except ServingRuntimeError:
            raise
        except Exception as error:
            raise ServingRuntimeError(
                f"serving runtime failed at {type(error).__name__}"
            ) from error
        finally:
            await adapter.aclose()

    async def _wait_ready(
        self,
        adapter: RunPodAdapter,
        handle: ProvisionHandle,
        *,
        session_id: str,
        model_id: str,
        endpoint_api_key: str,
        callback_key: str,
    ) -> tuple[str, float]:
        deadline = asyncio.get_running_loop().time() + min(
            self.settings.max_runtime_min * 60, 30 * 60
        )
        last_cost = 0.0
        async with httpx.AsyncClient(timeout=30) as client:
            while asyncio.get_running_loop().time() < deadline:
                status = await adapter.status(handle)
                last_cost = status.cost_accrued_usd
                if last_cost >= self.settings.budget_usd_cap:
                    raise ServingRuntimeError("serving session budget cap reached")
                callback = await asyncio.to_thread(self._read_callback, callback_key)
                if callback is not None:
                    url = _verified_callback(callback, self.settings.expected_tree_sha256)
                    if await _smoke_endpoint(
                        client,
                        url=url,
                        api_key=endpoint_api_key,
                        model_id=model_id,
                    ):
                        return f"{url.rstrip('/')}/v1", last_cost
                await asyncio.sleep(self.settings.poll_seconds)
        raise ServingRuntimeError("serving readiness exceeded 30 minutes")

    async def terminate(self, handle: ProvisionHandle) -> DeletionReceipt:
        adapter = RunPodAdapter(api_key_provider=self.credential_resolver)
        try:
            await _terminate_with_one_retry(adapter, handle)
            receipt = await confirm_deleted(adapter, handle, timeout_s=10 * 60, poll_s=15)
            schedule_billing(
                Path("runs/serving/v0.34.0/billing-schedule.json"),
                handle,
                receipt.checked_at,
            )
            return receipt
        finally:
            await adapter.aclose()

    def _prepare_download(self, session_id: str) -> "_PreparedDownload":
        artifact_key = "/".join(
            part
            for part in (
                self.prefix,
                "jobs",
                self.settings.model_job_id,
                "artifacts",
                f"{self.settings.model_artifact_name}.manifest.json",
            )
            if part
        )
        manifest = _json_object(
            self._s3.get_object(Bucket=self.bucket, Key=artifact_key)["Body"].read()
        )
        files = _verified_files(manifest, self.settings.expected_tree_sha256)
        downloadable = []
        for entry in files:
            downloadable.append(
                {
                    "path": entry["path"],
                    "size_bytes": entry["size_bytes"],
                    "sha256": entry["sha256"],
                    "url": self._s3.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": self.bucket, "Key": entry["key"]},
                        ExpiresIn=self.settings.presign_ttl_seconds,
                    ),
                }
            )
        base = "/".join(
            part for part in (self.prefix, "serving-sessions", session_id) if part
        )
        download_key = f"{base}/download-manifest.json"
        callback_key = f"{base}/tunnel.json"
        body = json.dumps(
            {
                "schema_version": 1,
                "tree_sha256": self.settings.expected_tree_sha256,
                "files": downloadable,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._s3.put_object(
            Bucket=self.bucket,
            Key=download_key,
            Body=body,
            ContentType="application/json",
        )
        return _PreparedDownload(
            manifest_url=self._s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": download_key},
                ExpiresIn=self.settings.presign_ttl_seconds,
            ),
            callback_put_url=self._s3.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": callback_key,
                    "ContentType": "application/json",
                },
                ExpiresIn=self.settings.presign_ttl_seconds,
            ),
            callback_key=callback_key,
        )

    def _read_callback(self, key: str) -> dict[str, object] | None:
        try:
            payload = self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as error:
            response = getattr(error, "response", {})
            code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
            if code in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise
        return _json_object(payload)


@dataclass(frozen=True, repr=False)
class _PreparedDownload:
    manifest_url: str
    callback_put_url: str
    callback_key: str


async def _smoke_endpoint(
    client: httpx.AsyncClient, *, url: str, api_key: str, model_id: str
) -> bool:
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        models = await client.get(f"{url.rstrip('/')}/v1/models", headers=headers)
        if models.status_code != 200:
            return False
        completion = await client.post(
            f"{url.rstrip('/')}/v1/chat/completions",
            headers=headers,
            json={
                "model": model_id,
                "messages": [{"role": "user", "content": "Reply with SELECT 1"}],
                "max_tokens": 16,
                "temperature": 0,
            },
        )
        return completion.status_code == 200 and bool(
            completion.json().get("choices")
        )
    except (httpx.HTTPError, ValueError):
        return False


def _verified_files(
    manifest: dict[str, object], expected_tree_sha256: str
) -> list[dict[str, object]]:
    values = manifest.get("files")
    if not isinstance(values, list) or not values:
        raise ServingRuntimeError("S3 model artifact manifest has no files")
    files: list[dict[str, object]] = []
    digest = hashlib.sha256()
    for item in sorted(values, key=lambda value: str(value.get("path")) if isinstance(value, dict) else ""):
        if not isinstance(item, dict):
            raise ServingRuntimeError("S3 model artifact manifest is invalid")
        path = item.get("path")
        sha = item.get("sha256")
        size = item.get("size_bytes")
        key = item.get("key")
        if (
            not isinstance(path, str)
            or not path
            or not isinstance(sha, str)
            or len(sha) != 64
            or not isinstance(size, int)
            or size < 0
            or not isinstance(key, str)
            or not key
        ):
            raise ServingRuntimeError("S3 model artifact identity is invalid")
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha.encode("ascii"))
        digest.update(b"\n")
        files.append(item)
    if digest.hexdigest() != expected_tree_sha256:
        raise ServingRuntimeError("S3 model artifact tree identity mismatch")
    return files


def _verified_callback(value: dict[str, object], expected_tree_sha256: str) -> str:
    url = value.get("url")
    if (
        not isinstance(url, str)
        or not url.startswith("https://")
        or not url.endswith(".trycloudflare.com")
        or value.get("tree_sha256") != expected_tree_sha256
    ):
        raise ServingRuntimeError("serving callback identity is invalid")
    return url


def _json_object(value: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ServingRuntimeError("serving S3 metadata is invalid JSON") from None
    if not isinstance(decoded, dict):
        raise ServingRuntimeError("serving S3 metadata is not an object")
    return decoded


def _s3_client(region: str | None):
    try:
        import boto3
    except ModuleNotFoundError as error:
        raise ServingRuntimeError("live serving requires boto3") from error
    return boto3.client("s3", region_name=region)


def _ephemeral_public_key() -> str:
    # RunPod accepts PUBLIC_KEY even though the prebuilt serving image does not
    # rely on SSH. A fresh non-secret placeholder prevents key reuse.
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ModuleNotFoundError as error:  # pragma: no cover - app dependency boundary.
        raise ServingRuntimeError("live serving requires cryptography") from error
    return Ed25519PrivateKey.generate().public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("ascii")


async def _terminate_with_one_retry(
    adapter,
    handle: ProvisionHandle,
    *,
    sleeper=asyncio.sleep,
) -> None:
    try:
        await adapter.terminate(handle)
    except ProvisionProviderError as error:
        if error.status_code in {401, 403}:
            raise
        # DELETE is idempotent. One bounded retry handles a transient provider
        # edge failure without creating a retry storm.
        await sleeper(2)
        await adapter.terminate(handle)


__all__ = [
    "MockServingRuntime",
    "ReadyRuntime",
    "RunPodServingRuntime",
    "ServingRuntime",
    "ServingRuntimeError",
]
