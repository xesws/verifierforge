"""Generic OpenAI-compatible client used by every external model caller."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from dotenv import find_dotenv, load_dotenv
from openai import OpenAI


DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_AUGMENT_MODEL = "z-ai/glm-5.2"


class LLMConfigurationError(RuntimeError):
    """Raised when required LLM environment configuration is absent."""


class LLMResponseError(RuntimeError):
    """Raised when an LLM response cannot be used safely."""


@dataclass(frozen=True)
class LLMSettings:
    """OpenAI-compatible endpoint configuration with a redacted API key."""

    api_key: str = field(repr=False)
    model: str = DEFAULT_AUGMENT_MODEL
    base_url: str = DEFAULT_LLM_BASE_URL

    def __post_init__(self) -> None:
        api_key = self.api_key.strip()
        if not api_key:
            raise LLMConfigurationError(
                "VF_LLM_API_KEY must be set before using LLM integrations."
            )
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "model", self.model.strip() or DEFAULT_AUGMENT_MODEL)
        object.__setattr__(self, "base_url", self.base_url.strip() or DEFAULT_LLM_BASE_URL)

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        dotenv_path: Path | None = None,
    ) -> "LLMSettings":
        """Read canonical settings, loading a local ``.env`` without overriding shell values."""
        if environ is None:
            resolved_dotenv_path = (
                str(dotenv_path) if dotenv_path is not None else find_dotenv(usecwd=True)
            )
            if resolved_dotenv_path:
                load_dotenv(resolved_dotenv_path, override=False)
            values = os.environ
        else:
            values = environ
        return cls(
            api_key=values.get("VF_LLM_API_KEY", ""),
            model=values.get("VF_AUGMENT_MODEL", DEFAULT_AUGMENT_MODEL),
            base_url=values.get("VF_LLM_BASE_URL", DEFAULT_LLM_BASE_URL),
        )


class LLMClient:
    """Call any OpenAI-compatible chat-completions endpoint through one surface."""

    def __init__(self, settings: LLMSettings, client: Any | None = None) -> None:
        self.settings = settings
        if client is not None:
            self._client = client
            return
        try:
            self._client = OpenAI(api_key=settings.api_key, base_url=settings.base_url)
        except Exception:
            raise LLMConfigurationError(
                "VF_LLM_BASE_URL could not initialize an OpenAI-compatible client."
            ) from None

    @property
    def model(self) -> str:
        """Return the configured model used when a caller does not override it."""
        return self.settings.model

    def complete(
        self,
        messages: Sequence[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        response_format: Mapping[str, Any] | None = None,
    ) -> str:
        """Return one non-empty assistant message without exposing request secrets."""
        request: dict[str, Any] = {
            "model": model or self.model,
            "messages": list(messages),
        }
        if temperature is not None:
            request["temperature"] = temperature
        if response_format is not None:
            request["response_format"] = dict(response_format)

        try:
            response = self._client.chat.completions.create(**request)
        except Exception as error:
            raise LLMResponseError(_request_error_message(error)) from None

        try:
            choices = response.choices
        except Exception:
            raise LLMResponseError("LLM returned an invalid completion response.") from None
        if not choices:
            raise LLMResponseError("LLM returned no completion choices.")

        try:
            content = choices[0].message.content
        except Exception:
            raise LLMResponseError("LLM returned an invalid completion response.") from None
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("LLM returned an empty completion.")
        return content

    def complete_json(
        self,
        messages: Sequence[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """Return a JSON-object completion from the configured compatible endpoint."""
        content = self.complete(
            messages,
            model=model,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        try:
            parsed = json.loads(_strip_json_fence(content))
        except json.JSONDecodeError:
            raise LLMResponseError(
                "LLM returned invalid JSON for a structured completion."
            ) from None
        if not isinstance(parsed, dict):
            raise LLMResponseError("LLM returned JSON that was not an object.")
        return parsed


def _request_error_message(error: Exception) -> str:
    """Describe a provider failure without copying credentials, prompts, or response text."""
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        return f"LLM request failed with HTTP status {status_code}."
    return "LLM request failed before a completion was returned."


def _strip_json_fence(content: str) -> str:
    """Accept a JSON code fence while keeping malformed text out of errors."""
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped
