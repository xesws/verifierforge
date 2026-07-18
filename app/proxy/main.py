"""A small, independently runnable OpenAI-compatible traffic proxy."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import sqlite3
import threading
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from app.gpt import LLMConfigurationError, LLMSettings
from app.proxy.clusters import cluster_id_for_system_prompt, system_prompt_hash
from app.proxy.frozen_nl2sql import FROZEN_TRAINING_POOL
from app.proxy.guardian import score_tuned_sql_completion
from app.proxy.routing import DEFAULT_TARGET_UPSTREAM, get_route
from app.proxy.traffic import (
    DEFAULT_DB_PATH,
    DEFAULT_PRICING_PATH,
    TrafficRecord,
    best_effort_cost,
    estimate_tokens,
    record_traffic,
)
from app.proxy.upstream import (
    ForwardedResponse,
    UpstreamRequestError,
    fake_chat_completion,
    fake_tuned_chat_completion,
    forward_real,
)


@dataclass(frozen=True)
class ProxySettings:
    upstream: str = "fake"
    tuned_upstream: str = "fake-tuned"
    tuned_api_key: str | None = field(default=None, repr=False)
    db_path: Path = DEFAULT_DB_PATH
    pricing_path: Path = DEFAULT_PRICING_PATH
    guardian_sample_rate: float = 0.20
    guardian_pool_path: Path = FROZEN_TRAINING_POOL
    guardian_rolling_window: int = 20

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ProxySettings":
        values = os.environ if environ is None else environ
        upstream = values.get("VF_PROXY_UPSTREAM", "fake").strip().lower() or "fake"
        if upstream not in {"fake", "real"}:
            raise ValueError("VF_PROXY_UPSTREAM must be fake or real")
        tuned_upstream = values.get("VF_PROXY_TUNED_UPSTREAM", "fake-tuned").strip() or "fake-tuned"
        tuned_api_key = values.get("VF_PROXY_TUNED_API_KEY", "").strip() or None
        if tuned_upstream.startswith(("http://", "https://")) and tuned_api_key is None:
            raise ValueError("VF_PROXY_TUNED_API_KEY is required for an HTTP tuned upstream")
        try:
            guardian_sample_rate = float(values.get("VF_PROXY_GUARDIAN_SAMPLE_RATE", "0.20"))
        except ValueError as error:
            raise ValueError("VF_PROXY_GUARDIAN_SAMPLE_RATE must be a number in [0, 1]") from error
        if not 0.0 <= guardian_sample_rate <= 1.0:
            raise ValueError("VF_PROXY_GUARDIAN_SAMPLE_RATE must be in [0, 1]")
        try:
            guardian_rolling_window = int(values.get("VF_PROXY_GUARDIAN_ROLLING_WINDOW", "20"))
        except ValueError as error:
            raise ValueError("VF_PROXY_GUARDIAN_ROLLING_WINDOW must be a positive integer") from error
        if guardian_rolling_window < 1:
            raise ValueError("VF_PROXY_GUARDIAN_ROLLING_WINDOW must be a positive integer")
        return cls(
            upstream=upstream,
            tuned_upstream=tuned_upstream,
            tuned_api_key=tuned_api_key,
            db_path=Path(values.get("VF_PROXY_DB_PATH", str(DEFAULT_DB_PATH))).expanduser(),
            pricing_path=Path(values.get("VF_PROXY_PRICING_PATH", str(DEFAULT_PRICING_PATH))).expanduser(),
            guardian_sample_rate=guardian_sample_rate,
            guardian_pool_path=Path(
                values.get("VF_PROXY_GUARDIAN_POOL_PATH", str(FROZEN_TRAINING_POOL))
            ).expanduser(),
            guardian_rolling_window=guardian_rolling_window,
        )


Recorder = Callable[[TrafficRecord], bool]
RealForwarder = Callable[..., ForwardedResponse]
RandomDraw = Callable[[], float]
GuardianScheduler = Callable[[Callable[[], None]], None]


@dataclass(frozen=True)
class RouteDecision:
    cluster_id: str | None
    route_path: str
    target_upstream: str | None


def create_app(
    *,
    settings: ProxySettings | None = None,
    recorder: Recorder = record_traffic,
    real_forwarder: RealForwarder = forward_real,
    canary_draw: RandomDraw = random.random,
    guardian_draw: RandomDraw = random.random,
    guardian_scheduler: GuardianScheduler | None = None,
) -> FastAPI:
    """Create an isolated proxy app; injected seams keep tests network-free."""
    resolved = settings or ProxySettings.from_env()
    schedule_guardian = guardian_scheduler or _schedule_background
    proxy = FastAPI(title="VerifierForge Proxy")

    @proxy.post("/v1/chat/completions")
    def chat_completions(request: dict[str, Any]) -> JSONResponse:
        _validate_request(request)
        started = time.perf_counter()
        system_prompt = _system_prompt(request)
        decision = _route_decision(system_prompt, resolved.db_path, canary_draw)
        try:
            forwarded = _forward(
                request,
                upstream=_selected_upstream(decision, resolved),
                settings=resolved,
                real_forwarder=real_forwarder,
            )
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
            system_prompt=system_prompt,
            route_path=decision.route_path,
        )
        _schedule_guardian_best_effort(
            schedule_guardian,
            decision=decision,
            request=request,
            response=forwarded.payload,
            status_code=forwarded.status_code,
            settings=resolved,
            draw=guardian_draw,
        )
        return JSONResponse(content=forwarded.payload, status_code=forwarded.status_code)

    return proxy


def _forward(
    request: Mapping[str, Any],
    *,
    upstream: str,
    settings: ProxySettings,
    real_forwarder: RealForwarder,
) -> ForwardedResponse:
    if upstream == "fake":
        return fake_chat_completion(request)
    if upstream == "fake-tuned":
        return fake_tuned_chat_completion(request, pool_path=settings.guardian_pool_path)
    if upstream == "real":
        llm = LLMSettings.from_env()
        return real_forwarder(request, base_url=llm.base_url, api_key=llm.api_key)
    if upstream.startswith(("http://", "https://")):
        if settings.tuned_api_key is None:
            raise UpstreamRequestError("HTTP tuned upstream requires VF_PROXY_TUNED_API_KEY")
        return real_forwarder(request, base_url=upstream, api_key=settings.tuned_api_key)
    raise UpstreamRequestError("configured upstream must be fake, fake-tuned, real, or an HTTP URL")


def _route_decision(system_prompt: str, db_path: Path, draw: RandomDraw) -> RouteDecision:
    cluster_id = cluster_id_for_system_prompt(system_prompt)
    if cluster_id is None:
        return RouteDecision(None, "default", None)
    try:
        route = get_route(cluster_id, db_path=db_path)
    except (OSError, sqlite3.Error, ValueError):
        return RouteDecision(cluster_id, "default", None)
    if route.enabled and draw() < route.canary_percent / 100:
        return RouteDecision(cluster_id, "tuned", route.target_upstream)
    return RouteDecision(cluster_id, "default", None)


def _selected_upstream(decision: RouteDecision, settings: ProxySettings) -> str:
    if decision.route_path != "tuned":
        return settings.upstream
    if decision.target_upstream in {None, DEFAULT_TARGET_UPSTREAM}:
        return settings.tuned_upstream
    return decision.target_upstream


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
    system_prompt: str,
    route_path: str,
) -> None:
    messages = [message for message in request.get("messages", []) if isinstance(message, Mapping)]
    input_tokens, output_tokens = _token_counts(messages, response)
    model = str(request["model"])
    record = TrafficRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        system_prompt_hash=system_prompt_hash(system_prompt),
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=round(latency_ms, 3),
        estimated_cost_usd=best_effort_cost(
            model, input_tokens, output_tokens, pricing_path=settings.pricing_path
        ),
        route_path=route_path,
    )
    try:
        recorder(record, db_path=settings.db_path)
    except Exception:
        # A locked or unavailable observability database is never a customer-facing failure.
        pass


def _schedule_guardian_best_effort(
    scheduler: GuardianScheduler,
    *,
    decision: RouteDecision,
    request: Mapping[str, Any],
    response: Mapping[str, Any],
    status_code: int,
    settings: ProxySettings,
    draw: RandomDraw,
) -> None:
    if decision.cluster_id != "data-pull-sql" or decision.route_path != "tuned":
        return
    if not 200 <= status_code < 300 or draw() >= settings.guardian_sample_rate:
        return
    prompt = _last_user_prompt(request)
    completion = _completion_content(response)
    if not prompt or not completion:
        return
    try:
        scheduler(
            lambda: score_tuned_sql_completion(
                cluster_id=decision.cluster_id,
                prompt=prompt,
                completion=completion,
                db_path=settings.db_path,
                pool_path=settings.guardian_pool_path,
                rolling_window=settings.guardian_rolling_window,
            )
        )
    except Exception:
        # Guardian scheduling is also observability: never fail a completion for it.
        pass


def _schedule_background(task: Callable[[], None]) -> None:
    threading.Thread(target=task, daemon=True, name="vf-proxy-guardian").start()


def _system_prompt(request: Mapping[str, Any]) -> str:
    return "\n".join(
        _message_content(message)
        for message in request.get("messages", [])
        if isinstance(message, Mapping) and message.get("role") == "system"
    )


def _last_user_prompt(request: Mapping[str, Any]) -> str:
    users = [
        _message_content(message)
        for message in request.get("messages", [])
        if isinstance(message, Mapping) and message.get("role") == "user"
    ]
    return users[-1] if users else ""


def _completion_content(response: Mapping[str, Any]) -> str:
    choices = response.get("choices", [])
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return ""
    message = choices[0].get("message")
    return _message_content(message) if isinstance(message, Mapping) else ""


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
