"""Small OpenRouter client for future GPT-powered product flows."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from openai import OpenAI


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "x-ai/grok-4.5"


class OpenRouterConfigurationError(RuntimeError):
    """Raised when the OpenRouter environment configuration is incomplete."""


class OpenRouterResponseError(RuntimeError):
    """Raised when OpenRouter returns no usable assistant text."""


@dataclass(frozen=True)
class OpenRouterSettings:
    """Non-secret OpenRouter configuration plus the required API key."""

    api_key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    base_url: str = OPENROUTER_BASE_URL
    app_url: str | None = None
    app_title: str | None = "VerifierForge"

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "OpenRouterSettings":
        values = os.environ if environ is None else environ
        api_key = values.get("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            raise OpenRouterConfigurationError(
                "OPENROUTER_API_KEY must be set before using OpenRouter integrations."
            )

        return cls(
            api_key=api_key,
            model=values.get("VF_GPT_MODEL", "").strip() or DEFAULT_MODEL,
            app_url=values.get("VF_APP_URL") or None,
            app_title=values.get("VF_APP_TITLE", "VerifierForge") or None,
        )

    def headers(self) -> dict[str, str]:
        """Return optional OpenRouter attribution headers."""
        headers: dict[str, str] = {}
        if self.app_url:
            headers["HTTP-Referer"] = self.app_url
        if self.app_title:
            headers["X-OpenRouter-Title"] = self.app_title
        return headers


class OpenRouterClient:
    """Call OpenRouter through the OpenAI-compatible chat-completions API."""

    def __init__(
        self, settings: OpenRouterSettings, client: Any | None = None
    ) -> None:
        self.settings = settings
        self._client = (
            client
            if client is not None
            else OpenAI(
                api_key=settings.api_key,
                base_url=settings.base_url,
                default_headers=settings.headers(),
            )
        )

    def complete(
        self,
        messages: Sequence[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        response_format: Mapping[str, Any] | None = None,
    ) -> str:
        """Return one non-empty assistant message from OpenRouter."""
        request: dict[str, Any] = {
            "model": model or self.settings.model,
            "messages": list(messages),
        }
        if temperature is not None:
            request["temperature"] = temperature
        if response_format is not None:
            request["response_format"] = dict(response_format)

        response = self._client.chat.completions.create(**request)
        if not response.choices:
            raise OpenRouterResponseError("OpenRouter returned no completion choices.")

        content = response.choices[0].message.content
        if not content or not content.strip():
            raise OpenRouterResponseError("OpenRouter returned an empty completion.")
        return content

    def complete_json(
        self,
        messages: Sequence[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """Return a JSON-object completion or raise a provider-specific error."""
        content = self.complete(
            messages,
            model=model,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        payload = _strip_json_fence(content)
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as error:
            raise OpenRouterResponseError(
                "OpenRouter returned invalid JSON for a structured completion."
            ) from error
        if not isinstance(parsed, dict):
            raise OpenRouterResponseError(
                "OpenRouter returned JSON that was not an object."
            )
        return parsed


def _strip_json_fence(content: str) -> str:
    """Accept a JSON code fence while keeping malformed text visible to callers."""
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped
