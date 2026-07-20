"""RunPod REST adapter with strict VerifierForge ownership filtering."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Mapping

import httpx

from app.provisioning.errors import ProvisionNoCapacity, ProvisionProviderError
from core.provisioning_contracts import (
    BLOCKED_GPU_MODEL_FRAGMENTS,
    DEFAULT_GPU_CANDIDATES,
    ProvisionHandle,
    ProvisionProvider,
    ProvisionSpec,
    ProvisionState,
    ProvisionStatus,
)


RUNPOD_API_BASE_URL = "https://rest.runpod.io/v1"
RUNPOD_GRAPHQL_URL = "https://api.runpod.io/graphql"
MANAGED_NAME_PREFIX = "vf-auto-"
OWNER_MARKER_KEY = "VF_MANAGED_BY"
OWNER_MARKER_VALUE = "verifierforge-p2"
RUNPOD_IMAGE = "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404"
_ERROR_BODY_LIMIT = 4000
_CAPACITY_QUERY = """
query Capacity($ids: [String!], $community: GpuLowestPriceInput!, $secure: GpuLowestPriceInput!) {
  gpuTypes(input: {ids: $ids}) {
    id
    displayName
    memoryInGb
    secureCloud
    communityCloud
    securePrice
    communityPrice
    maxGpuCount
    maxGpuCountCommunityCloud
    maxGpuCountSecureCloud
    community: lowestPrice(input: $community) {
      gpuTypeId
      uninterruptablePrice
      stockStatus
      availableGpuCounts
      maxUnreservedGpuCount
      countryCode
    }
    secure: lowestPrice(input: $secure) {
      gpuTypeId
      uninterruptablePrice
      stockStatus
      availableGpuCounts
      maxUnreservedGpuCount
      countryCode
    }
  }
}
"""


@dataclass(frozen=True)
class RunPodBilling:
    amount_usd: float
    time_billed_ms: int
    records: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class RunPodGPUOffer:
    gpu_type_id: str
    display_name: str
    cloud_type: str
    hourly_price_usd: float
    stock_status: str


@dataclass(frozen=True)
class RunPodRuntimeConfig:
    """Ephemeral container launch details excluded from ProvisionSpec/audit."""

    http_ports: tuple[int, ...] = ()
    docker_entrypoint: tuple[str, ...] | None = None
    docker_start_cmd: tuple[str, ...] | None = None
    ephemeral_env_provider: Callable[[], Mapping[str, str]] | None = None

    def __post_init__(self) -> None:
        if len(set(self.http_ports)) != len(self.http_ports) or any(
            port < 1 or port > 65535 for port in self.http_ports
        ):
            raise ValueError("http_ports must contain unique TCP ports")
        command_values = (self.docker_entrypoint, self.docker_start_cmd)
        if any(
            not value
            for values in command_values
            if values is not None
            for value in values
        ):
            raise ValueError("container command entries must be non-empty")


class RunPodAdapter:
    """One-GPU RunPod adapter; API credentials never cross this local boundary."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        api_key_provider: Callable[[], str] | None = None,
        client: httpx.AsyncClient | None = None,
        base_url: str = RUNPOD_API_BASE_URL,
        graphql_url: str = RUNPOD_GRAPHQL_URL,
        timeout_s: float = 30.0,
        create_timeout_s: float | None = None,
        runtime: RunPodRuntimeConfig | None = None,
    ) -> None:
        if api_key_provider is not None and api_key is not None:
            raise ValueError("provide api_key or api_key_provider, not both")
        if api_key_provider is None:
            value = (api_key or "").strip()
            if not value:
                raise ValueError("RUNPOD_API_KEY is required")
            self._api_key_provider = lambda: value
        else:
            self._api_key_provider = api_key_provider
        self.base_url = base_url.rstrip("/")
        self.graphql_url = graphql_url
        self.create_timeout_s = create_timeout_s or timeout_s
        self.runtime = runtime or RunPodRuntimeConfig()
        if self.create_timeout_s <= 0:
            raise ValueError("create_timeout_s must be positive")
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout_s)

    async def __aenter__(self) -> "RunPodAdapter":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def provision(self, spec: ProvisionSpec) -> ProvisionHandle:
        if spec.provider is not ProvisionProvider.RUNPOD:
            raise ValueError("RunPodAdapter only accepts provider=runpod")
        offers = await self.available_gpu_offers(spec)
        if not offers:
            raise ProvisionNoCapacity(
                "no_capacity: no approved GPU candidate is currently available"
            )
        for offer in offers:
            try:
                pod = await self._create_candidate(spec, offer)
            except (ProvisionProviderError, TimeoutError):
                recovered = await self._recover_created_pod(spec)
                if recovered is None:
                    continue
                pod = recovered
            return await self._accept_created_pod(pod, spec=spec, offer=offer)
        raise ProvisionNoCapacity(
            f"no_capacity: all {len(offers)} available GPU candidates failed to create"
        )

    async def available_gpu_offers(self, spec: ProvisionSpec) -> list[RunPodGPUOffer]:
        """Query live stock and return one cheapest cloud offer per approved GPU."""
        candidates = DEFAULT_GPU_CANDIDATES[ProvisionProvider.RUNPOD][spec.gpu_class]
        lowest_price_input: dict[str, Any] = {
            "gpuCount": 1,
            "supportPublicIp": True,
        }
        if spec.region_pref:
            lowest_price_input["dataCenterId"] = ",".join(spec.region_pref)
        body = await self._graphql_json(
            query=_CAPACITY_QUERY,
            variables={
                "ids": list(candidates),
                "community": {**lowest_price_input, "secureCloud": False},
                "secure": {**lowest_price_input, "secureCloud": True},
            },
        )
        data = body.get("data")
        values = data.get("gpuTypes") if isinstance(data, dict) else None
        if not isinstance(values, list) or not all(isinstance(value, dict) for value in values):
            raise ProvisionProviderError("RunPod capacity response has invalid gpuTypes")
        by_id = {str(value.get("id")): value for value in values}
        ranked: list[tuple[float, int, RunPodGPUOffer]] = []
        for index, gpu_type_id in enumerate(candidates):
            if _blocked_gpu_model(gpu_type_id):
                continue
            gpu = by_id.get(gpu_type_id)
            if gpu is None:
                continue
            offers = [
                offer
                for cloud, field in (("COMMUNITY", "community"), ("SECURE", "secure"))
                if (offer := _gpu_offer(gpu, gpu_type_id, cloud, field)) is not None
            ]
            if not offers:
                continue
            selected = min(offers, key=lambda offer: (offer.hourly_price_usd, offer.cloud_type))
            ranked.append((selected.hourly_price_usd, index, selected))
        return [offer for _price, _index, offer in sorted(ranked)]

    async def _create_candidate(
        self, spec: ProvisionSpec, offer: RunPodGPUOffer
    ) -> dict[str, Any]:
        payload = self._create_payload(spec, offer)
        try:
            body = await asyncio.wait_for(
                self._request_json("POST", "/pods", json=payload, expected={201}),
                timeout=self.create_timeout_s,
            )
        except asyncio.TimeoutError as error:
            raise TimeoutError("RunPod candidate create timed out") from error
        return _object(body, "RunPod create response")

    def _create_payload(
        self, spec: ProvisionSpec, offer: RunPodGPUOffer
    ) -> dict[str, Any]:
        ephemeral_env = (
            dict(self.runtime.ephemeral_env_provider())
            if self.runtime.ephemeral_env_provider is not None
            else {}
        )
        if any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value
            for key, value in ephemeral_env.items()
        ):
            raise ProvisionProviderError("ephemeral container environment is invalid")
        ports = ["22/tcp"]
        for port in spec.ports:
            if port == 22:
                continue
            protocol = "http" if port in self.runtime.http_ports else "tcp"
            ports.append(f"{port}/{protocol}")
        payload: dict[str, Any] = {
            "name": self._managed_name(spec.job_id),
            "cloudType": offer.cloud_type,
            "computeType": "GPU",
            "gpuCount": 1,
            "gpuTypeIds": [offer.gpu_type_id],
            "gpuTypePriority": "custom",
            "imageName": spec.image,
            "containerDiskInGb": spec.container_disk_gb,
            "volumeInGb": 0,
            "interruptible": False,
            "supportPublicIp": True,
            "ports": ports,
            "env": {
                **spec.env,
                **ephemeral_env,
                OWNER_MARKER_KEY: OWNER_MARKER_VALUE,
                "VF_JOB_ID": spec.job_id,
                "VF_APPROVAL_ID": spec.approval_id,
                "PUBLIC_KEY": spec.ssh_pubkey,
            },
        }
        if self.runtime.docker_entrypoint is not None:
            payload["dockerEntrypoint"] = list(self.runtime.docker_entrypoint)
        if self.runtime.docker_start_cmd is not None:
            payload["dockerStartCmd"] = list(self.runtime.docker_start_cmd)
        if spec.region_pref:
            payload["dataCenterIds"] = spec.region_pref
        return payload

    async def _recover_created_pod(self, spec: ProvisionSpec) -> dict[str, Any] | None:
        expected_name = self._managed_name(spec.job_id)
        matches = [
            pod for pod in await self.list_account_pods() if pod.get("name") == expected_name
        ]
        if not matches:
            return None
        if len(matches) != 1 or not self._is_managed(
            matches[0], expected_name=expected_name, expected_job_id=spec.job_id
        ):
            raise ProvisionProviderError(
                "RunPod create reconciliation failed ownership verification"
            )
        return matches[0]

    async def _accept_created_pod(
        self,
        pod: dict[str, Any],
        *,
        spec: ProvisionSpec,
        offer: RunPodGPUOffer,
    ) -> ProvisionHandle:
        external_id = _required_text(pod, "id")
        expected_name = self._managed_name(spec.job_id)
        if not self._is_managed(pod, expected_name=expected_name):
            refreshed = await self.get_pod(external_id)
            if refreshed is not None:
                pod = refreshed
        if not self._is_managed(pod, expected_name=expected_name):
            # The provider response must echo enough identity to prove ownership.
            try:
                await self._request("DELETE", f"/pods/{external_id}", expected={204, 404})
            finally:
                raise ProvisionProviderError("RunPod create response failed ownership verification")
        return self._handle_from_pod(pod, spec=spec, offer=offer)

    async def status(self, handle: ProvisionHandle) -> ProvisionStatus:
        pod = await self.get_pod(handle.external_id)
        if pod is None:
            return ProvisionStatus(
                state=ProvisionState.TERMINATED,
                detail="RunPod resource is absent",
            )
        if not self._is_managed(pod, expected_job_id=handle.job_id):
            raise ProvisionProviderError("RunPod status response failed ownership verification")

        desired = str(pod.get("desiredStatus") or pod.get("status") or "").upper()
        uptime_seconds = _uptime_seconds(pod)
        uptime_min = max(0, math.ceil(uptime_seconds / 60)) if uptime_seconds else 0
        hourly = _hourly_price(pod)
        cost = round(hourly * uptime_seconds / 3600, 6)
        ssh = _ssh_endpoint(pod)
        error_text = _provider_error(pod)
        if error_text:
            state = ProvisionState.FAILED
        elif desired in {"EXITED", "TERMINATED", "STOPPED"}:
            state = ProvisionState.TERMINATED
        elif ssh is not None:
            state = ProvisionState.BOOTSTRAPPING
        else:
            state = ProvisionState.PROVISIONING
        return ProvisionStatus(
            state=state,
            ssh=ssh,
            cost_accrued_usd=cost,
            uptime_min=uptime_min,
            detail=error_text or f"RunPod desiredStatus={desired or 'UNKNOWN'}",
        )

    async def terminate(self, handle: ProvisionHandle) -> None:
        pod = await self.get_pod(handle.external_id)
        if pod is None:
            return
        if not self._is_managed(pod, expected_job_id=handle.job_id):
            raise ProvisionProviderError("refusing to terminate a RunPod resource without matching ownership")
        await self._request("DELETE", f"/pods/{handle.external_id}", expected={204, 404})

    async def list_active(self) -> list[ProvisionHandle]:
        pods = await self.list_account_pods()
        handles: list[ProvisionHandle] = []
        for pod in pods:
            if not self._is_managed(pod):
                continue
            desired = str(pod.get("desiredStatus") or pod.get("status") or "").upper()
            if desired in {"EXITED", "TERMINATED", "STOPPED"}:
                continue
            job_id = _env(pod).get("VF_JOB_ID")
            approval_id = _env(pod).get("VF_APPROVAL_ID")
            if not job_id or not approval_id:
                continue
            handles.append(
                ProvisionHandle(
                    provider=ProvisionProvider.RUNPOD,
                    external_id=_required_text(pod, "id"),
                    job_id=job_id,
                    approval_id=approval_id,
                    region=_region(pod),
                    ssh=_ssh_endpoint(pod),
                    labels={"name": str(pod.get("name", "")), OWNER_MARKER_KEY: OWNER_MARKER_VALUE},
                    created_at=_created_at(pod),
                )
            )
        return handles

    async def list_account_pods(self) -> list[dict[str, Any]]:
        body = await self._request_json("GET", "/pods", expected={200})
        if isinstance(body, list):
            values = body
        elif isinstance(body, dict):
            values = body.get("pods") or body.get("items") or []
        else:
            raise ProvisionProviderError("RunPod list response is not an object or list")
        if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
            raise ProvisionProviderError("RunPod list response has an invalid pods collection")
        return list(values)

    async def get_pod(self, external_id: str) -> dict[str, Any] | None:
        response = await self._request("GET", f"/pods/{external_id}", expected={200, 404})
        if response.status_code == 404:
            return None
        return _object(_decode_json(response), "RunPod pod response")

    async def billing(
        self,
        external_id: str,
        *,
        start_time: datetime,
    ) -> RunPodBilling:
        body = await self._request_json(
            "GET",
            "/billing/pods",
            params={
                "podId": external_id,
                "startTime": start_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "grouping": "podId",
                "bucketSize": "hour",
            },
            expected={200},
        )
        if isinstance(body, list):
            records = body
        elif isinstance(body, dict):
            records = body.get("records") or body.get("billingRecords") or body.get("data") or []
        else:
            records = []
        if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
            raise ProvisionProviderError("RunPod billing response has invalid records")
        amount = sum(_number(record, "amount", "cost", "amountUsd") for record in records)
        billed_ms = int(sum(_number(record, "timeBilledMs", "time_billed_ms") for record in records))
        return RunPodBilling(amount_usd=round(amount, 6), time_billed_ms=billed_ms, records=tuple(records))

    def _handle_from_pod(
        self,
        pod: Mapping[str, Any],
        *,
        spec: ProvisionSpec,
        offer: RunPodGPUOffer,
    ) -> ProvisionHandle:
        provider_price = _hourly_price(pod)
        hourly_price = provider_price if provider_price > 0 else offer.hourly_price_usd
        return ProvisionHandle(
            provider=ProvisionProvider.RUNPOD,
            external_id=_required_text(pod, "id"),
            job_id=spec.job_id,
            approval_id=spec.approval_id,
            region=_region(pod),
            ssh=_ssh_endpoint(pod),
            labels={
                "name": str(pod.get("name", "")),
                OWNER_MARKER_KEY: OWNER_MARKER_VALUE,
                "gpu_model": offer.gpu_type_id,
                "gpu_display_name": offer.display_name,
                "cloud_type": offer.cloud_type,
                "hourly_price_usd": f"{hourly_price:.6f}",
            },
            created_at=_created_at(pod),
        )

    def _is_managed(
        self,
        pod: Mapping[str, Any],
        *,
        expected_name: str | None = None,
        expected_job_id: str | None = None,
    ) -> bool:
        name = str(pod.get("name") or "")
        environment = _env(pod)
        if not name.startswith(MANAGED_NAME_PREFIX):
            return False
        if expected_name is not None and name != expected_name:
            return False
        if environment.get(OWNER_MARKER_KEY) != OWNER_MARKER_VALUE:
            return False
        if expected_job_id is not None and environment.get("VF_JOB_ID") != expected_job_id:
            return False
        return True

    @staticmethod
    def _managed_name(job_id: str) -> str:
        return f"{MANAGED_NAME_PREFIX}{job_id}"

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        expected: set[int],
        **kwargs: Any,
    ) -> Any:
        return _decode_json(await self._request(method, path, expected=expected, **kwargs))

    async def _graphql_json(
        self,
        *,
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        api_key = self._api_key_provider().strip()
        if not api_key:
            raise ProvisionProviderError("RunPod credential is unavailable")
        try:
            response = await self.client.request(
                "POST",
                self.graphql_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"query": query, "variables": variables},
            )
        except httpx.HTTPError as error:
            raise ProvisionProviderError(
                f"RunPod capacity query transport failed: {type(error).__name__}"
            ) from error
        if response.status_code != 200:
            body = self._redact(response.text[:_ERROR_BODY_LIMIT], api_key)
            raise ProvisionProviderError(
                f"RunPod capacity query returned HTTP {response.status_code}: {body}",
                status_code=response.status_code,
                provider_body=body,
            )
        decoded = _decode_json(response)
        if not isinstance(decoded, dict):
            raise ProvisionProviderError("RunPod capacity response is not an object")
        if decoded.get("errors"):
            body = self._redact(
                json.dumps(decoded["errors"], ensure_ascii=True)[:_ERROR_BODY_LIMIT],
                api_key,
            )
            raise ProvisionProviderError(
                f"RunPod capacity query returned GraphQL errors: {body}",
                provider_body=body,
            )
        return decoded

    async def _request(
        self,
        method: str,
        path: str,
        *,
        expected: set[int],
        **kwargs: Any,
    ) -> httpx.Response:
        api_key = self._api_key_provider().strip()
        if not api_key:
            raise ProvisionProviderError("RunPod credential is unavailable")
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {api_key}"
        headers["Content-Type"] = "application/json"
        try:
            response = await self.client.request(
                method, f"{self.base_url}{path}", headers=headers, **kwargs
            )
        except httpx.HTTPError as error:
            raise ProvisionProviderError(
                f"RunPod {method} {path} transport failed: {type(error).__name__}"
            ) from error
        if response.status_code not in expected:
            body = self._redact(response.text[:_ERROR_BODY_LIMIT], api_key)
            raise ProvisionProviderError(
                f"RunPod {method} {path} returned HTTP {response.status_code}: {body}",
                status_code=response.status_code,
                provider_body=body,
            )
        return response

    def _redact(self, value: str, api_key: str) -> str:
        sanitized = value.replace(api_key, "[REDACTED]")
        if self.runtime.ephemeral_env_provider is not None:
            for secret in self.runtime.ephemeral_env_provider().values():
                if secret:
                    sanitized = sanitized.replace(secret, "[REDACTED]")
        return sanitized


def _decode_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as error:
        raise ProvisionProviderError("RunPod response was not valid JSON") from error


def _gpu_offer(
    gpu: Mapping[str, Any],
    gpu_type_id: str,
    cloud_type: str,
    field: str,
) -> RunPodGPUOffer | None:
    value = gpu.get(field)
    if not isinstance(value, Mapping):
        return None
    returned_id = value.get("gpuTypeId")
    price = value.get("uninterruptablePrice")
    stock_status = value.get("stockStatus")
    if returned_id != gpu_type_id or isinstance(price, bool) or not isinstance(
        price, (int, float)
    ):
        return None
    numeric_price = float(price)
    if not math.isfinite(numeric_price) or numeric_price <= 0 or not stock_status:
        return None
    return RunPodGPUOffer(
        gpu_type_id=gpu_type_id,
        display_name=str(gpu.get("displayName") or gpu_type_id),
        cloud_type=cloud_type,
        hourly_price_usd=numeric_price,
        stock_status=str(stock_status),
    )


def _blocked_gpu_model(gpu_type_id: str) -> bool:
    lowered = gpu_type_id.lower().replace("-", "_")
    return any(fragment in lowered for fragment in BLOCKED_GPU_MODEL_FRAGMENTS)


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProvisionProviderError(f"{label} is not an object")
    return value


def _required_text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ProvisionProviderError(f"RunPod response is missing {key}")
    return result


def _env(pod: Mapping[str, Any]) -> dict[str, str]:
    value = pod.get("env") or pod.get("environment") or {}
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    if isinstance(value, list):
        result: dict[str, str] = {}
        for item in value:
            if isinstance(item, dict) and item.get("key") is not None:
                result[str(item["key"])] = str(item.get("value", ""))
        return result
    return {}


def _ssh_endpoint(pod: Mapping[str, Any]) -> str | None:
    public_ip = pod.get("publicIp") or pod.get("public_ip")
    port: Any = None
    mappings = pod.get("portMappings") or pod.get("port_mappings") or {}
    if isinstance(mappings, dict):
        port = mappings.get("22") or mappings.get(22) or mappings.get("22/tcp")
    runtime = pod.get("runtime")
    if (not public_ip or not port) and isinstance(runtime, dict):
        for entry in runtime.get("ports") or []:
            if not isinstance(entry, dict):
                continue
            if int(entry.get("privatePort") or 0) != 22:
                continue
            public_ip = public_ip or entry.get("ip")
            port = port or entry.get("publicPort")
            break
    if not public_ip or not port:
        return None
    return f"root@{public_ip}:{int(port)}"


def _uptime_seconds(pod: Mapping[str, Any]) -> float:
    runtime = pod.get("runtime")
    if isinstance(runtime, dict):
        value = runtime.get("uptimeInSeconds") or runtime.get("uptime_seconds") or 0
        return max(0.0, float(value or 0))
    direct = pod.get("uptimeInSeconds")
    if direct is not None:
        return max(0.0, float(direct or 0))
    started = pod.get("lastStartedAt")
    if isinstance(started, str):
        value = _provider_datetime(started)
        if value is not None:
            return max(0.0, (datetime.now(timezone.utc) - value).total_seconds())
    return 0.0


def _hourly_price(pod: Mapping[str, Any]) -> float:
    for container in (pod, pod.get("gpu") or {}, pod.get("machine") or {}):
        if not isinstance(container, Mapping):
            continue
        for key in ("adjustedCostPerHr", "costPerHr", "costPerHour", "communityPrice", "securePrice"):
            value = container.get(key)
            if value is not None:
                return max(0.0, float(value))
    return 0.0


def _provider_error(pod: Mapping[str, Any]) -> str:
    for key in ("error", "lastError", "message"):
        value = pod.get(key)
        if value:
            return str(value)[:1000]
    return ""


def _region(pod: Mapping[str, Any]) -> str | None:
    value = pod.get("dataCenterId") or pod.get("region")
    if value:
        return str(value)[:64]
    machine = pod.get("machine")
    if isinstance(machine, dict) and machine.get("dataCenterId"):
        return str(machine["dataCenterId"])[:64]
    return None


def _created_at(pod: Mapping[str, Any]) -> datetime:
    value = pod.get("createdAt") or pod.get("created_at") or pod.get("lastStartedAt")
    if isinstance(value, str):
        parsed = _provider_datetime(value)
        if parsed is not None:
            return parsed
    return datetime.now(timezone.utc)


def _provider_datetime(value: str) -> datetime | None:
    normalized = value.strip()
    if normalized.endswith(" UTC"):
        normalized = normalized[:-4]
    timestamp, separator, offset = normalized.rpartition(" ")
    if separator and len(offset) in {5, 6} and offset[0] in {"+", "-"}:
        normalized = timestamp + offset
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: Mapping[str, Any], *keys: str) -> float:
    for key in keys:
        raw = value.get(key)
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


__all__ = [
    "MANAGED_NAME_PREFIX",
    "OWNER_MARKER_KEY",
    "OWNER_MARKER_VALUE",
    "RUNPOD_API_BASE_URL",
    "RUNPOD_IMAGE",
    "RunPodAdapter",
    "RunPodBilling",
    "RunPodRuntimeConfig",
]
