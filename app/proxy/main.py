"""A small, independently runnable OpenAI-compatible traffic proxy."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from app.gpt import LLMConfigurationError, LLMSettings
from app.proxy.traffic import (
    DEFAULT_DB_PATH,
    DEFAULT_PRICING_PATH,
    TrafficRecord,
    best_effort_cost,
    estimate_tokens,
    record_traffic,
)
from app.proxy.upstream import ForwardedResponse, UpstreamRequestError, fake_chat_completion, forward_real


@dataclass(frozen=True)
class ProxySettings:
    upstream: str = "fake"
    db_path: Path = DEFAULT_DB_PATH
    pricing_path: Path = DEFAULT_PRICING_PATH

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ProxySettings":
        values = os.environ if environ is None else environ
        upstream = values.get("VF_PROXY_UPSTREAM", "fake").strip().lower() or "fake"
        if upstream not in {"fake", "real"}:
            raise ValueError("VF_PROXY_UPSTREAM must be fake or real")
        return cls(
            upstream=upstream,
            db_path=Path(values.get("VF_PROXY_DB_PATH", str(DEFAULT_DB_PATH))).expanduser(),
            pricing_path=Path(values.get("VF_PROXY_PRICING_PATH", str(DEFAULT_PRICING_PATH))).expanduser(),
        )


Recorder = Callable[[TrafficRecord], bool]
RealForwarder = Callable[..., ForwardedResponse]


def create_app(
    *,
    settings: ProxySettings | None = None,
    recorder: Recorder = record_traffic,
    real_forwarder: RealForwarder = forward_real,
) -> FastAPI:
    """Create an isolated proxy app; injected seams keep tests network-free."""
    resolved = settings or ProxySettings.from_env()
    proxy = FastAPI(title="VerifierForge Proxy")

    @proxy.post("/v1/chat/completions")
    def chat_completions(request: dict[str, Any]) -> JSONResponse:
        _validate_request(request)
        started = time.perf_counter()
        try:
            if resolved.upstream == "fake":
                forwarded = fake_chat_completion(request)
            else:
                llm = LLMSettings.from_env()
                forwarded = real_forwarder(request, base_url=llm.base_url, api_key=llm.api_key)
        except (LLMConfigurationError, UpstreamRequestError) as error:
            forwarded = ForwardedResponse(
                502,
                {"error": {"message": str(error), "type": "upstream_error"}},
            )

        _record_best_effort(
            recorder,
            request=request,
            response=forwarded.payload,
            settings=resolved,
            latency_ms=(time.perf_counter() - started) * 1_000,
        )
        return JSONResponse(content=forwarded.payload, status_code=forwarded.status_code)

    return proxy


def _validate_request(request: Mapping[str, Any]) -> None:
    if not isinstance(request.get("model"), str) or not str(request["model"]).strip():
        raise HTTPException(status_code=400, detail="model must be a non-empty string")
    if not isinstance(request.get("messages"), list):
        raise HTTPException(status_code=400, detail="messages must be a list")


def _record_best_effort(
    recorder: Recorder,
    *,
    request: Mapping[str, Any],
    response: Mapping[str, Any],
    settings: ProxySettings,
    latency_ms: float,
) -> None:
    messages = [message for message in request.get("messages", []) if isinstance(message, Mapping)]
    system_prompt = "\n".join(_message_content(message) for message in messages if message.get("role") == "system")
    input_tokens, output_tokens = _token_counts(messages, response)
    model = str(request["model"])
    record = TrafficRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        system_prompt_hash=hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=round(latency_ms, 3),
        estimated_cost_usd=best_effort_cost(
            model, input_tokens, output_tokens, pricing_path=settings.pricing_path
        ),
    )
    try:
        recorder(record, db_path=settings.db_path)
    except Exception:
        # A locked or unavailable observability database is never a customer-facing failure.
        pass


def _token_counts(messages: list[Mapping[str, Any]], response: Mapping[str, Any]) -> tuple[int, int]:
    usage = response.get("usage")
    if isinstance(usage, Mapping):
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if _token_count(prompt) is not None and _token_count(completion) is not None:
            return int(prompt), int(completion)
    input_text = "\n".join(_message_content(message) for message in messages)
    choices = response.get("choices", [])
    first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else {}
    message = first.get("message", {}) if isinstance(first, Mapping) else {}
    output = _message_content(message) if isinstance(message, Mapping) else ""
    return estimate_tokens(input_text), estimate_tokens(output)


def _token_count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _message_content(message: Mapping[str, Any]) -> str:
    content = message.get("content", "")
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)


app = create_app()
