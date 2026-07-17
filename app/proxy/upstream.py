"""Fake and real OpenAI-compatible proxy upstreams."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.proxy.traffic import estimate_tokens


class UpstreamRequestError(RuntimeError):
    """A real compatible upstream could not provide a JSON completion response."""


@dataclass(frozen=True)
class ForwardedResponse:
    status_code: int
    payload: dict[str, Any]


def fake_chat_completion(request: Mapping[str, Any]) -> ForwardedResponse:
    """Return a deterministic, OpenAI-shaped completion without any network call."""
    identity = hashlib.sha256(_canonical_json(request).encode("utf-8")).hexdigest()
    model = request.get("model") if isinstance(request.get("model"), str) else "vf-fake"
    prompt_text = "\n".join(_message_text(message) for message in _messages(request))
    content = f"vf-fake-completion-{identity[:16]}"
    input_tokens = estimate_tokens(prompt_text)
    output_tokens = estimate_tokens(content)
    return ForwardedResponse(
        status_code=200,
        payload={
            "id": f"chatcmpl-vf-{identity[:20]}",
            "object": "chat.completion",
            "created": int(identity[:8], 16),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
        },
    )


def forward_real(
    request: Mapping[str, Any], *, base_url: str, api_key: str
) -> ForwardedResponse:
    """Forward a non-streaming OpenAI request unchanged to its configured upstream."""
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    outgoing = Request(
        endpoint,
        data=json.dumps(dict(request)).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(outgoing, timeout=60) as response:  # noqa: S310 - configured operator endpoint.
            return ForwardedResponse(response.status, _decode_payload(response.read()))
    except HTTPError as error:
        return ForwardedResponse(error.code, _decode_payload(error.read()))
    except (OSError, URLError) as error:
        raise UpstreamRequestError("configured upstream did not return a completion") from error


def _decode_payload(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UpstreamRequestError("upstream returned a non-JSON completion response") from error
    if not isinstance(payload, dict):
        raise UpstreamRequestError("upstream returned a JSON response that was not an object")
    return payload


def _messages(request: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    messages = request.get("messages", [])
    return [message for message in messages if isinstance(message, Mapping)] if isinstance(messages, list) else []


def _message_text(message: Mapping[str, Any]) -> str:
    content = message.get("content", "")
    return content if isinstance(content, str) else _canonical_json(content)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
