"""Generic OpenAI-compatible client used by every external model caller."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from dotenv import find_dotenv, load_dotenv
from openai import OpenAI


DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_AUGMENT_MODEL = "z-ai/glm-5.2"


class LLMConfigurationError(RuntimeError):
    """Raised when required LLM environment configuration is absent."""


class LLMResponseError(ValueError):
    """Raised when an LLM response cannot be parsed or used safely."""


class LLMRequestError(RuntimeError):
    """A provider failure with a redacted, bounded audit payload."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        provider_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider_body = provider_body


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


@dataclass(frozen=True)
class EvalSettings:
    """Evaluation-only endpoint settings, intentionally isolated from augmentation.

    This class deliberately does not load ``.env``: Gate A must be explicit about
    the endpoint it measures, and must never inherit the paid OpenRouter client
    configuration used by augmentation or Copilot.
    """

    api_key: str = field(default="vf-local-eval", repr=False)
    model: str = ""
    base_url: str = ""

    def __post_init__(self) -> None:
        model = self.model.strip()
        base_url = self.base_url.strip()
        if not base_url:
            raise LLMConfigurationError("VF_EVAL_BASE_URL must be set for Gate A.")
        if not model:
            raise LLMConfigurationError("VF_EVAL_MODEL must be set for Gate A.")
        api_key = self.api_key.strip() or "vf-local-eval"
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "base_url", base_url)

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "EvalSettings":
        """Read only explicit Gate A variables, without dotenv or LLM fallback."""
        values = os.environ if environ is None else environ
        return cls(
            api_key=values.get("VF_EVAL_API_KEY", "vf-local-eval"),
            model=values.get("VF_EVAL_MODEL", ""),
            base_url=values.get("VF_EVAL_BASE_URL", ""),
        )


class LLMClient:
    """Call any OpenAI-compatible chat-completions endpoint through one surface."""

    def __init__(self, settings: LLMSettings | EvalSettings, client: Any | None = None) -> None:
        self.settings = settings
        if client is not None:
            self._client = client
            return
        try:
            self._client = OpenAI(api_key=settings.api_key, base_url=settings.base_url)
        except Exception as error:
            raise LLMConfigurationError(
                "The configured base_url could not initialize an OpenAI-compatible client."
            ) from error

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
            raise LLMRequestError(
                _request_error_message(error),
                status_code=_provider_status_code(error),
                provider_body=_provider_error_body(error),
            ) from error

        try:
            choices = response.choices
        except Exception as error:
            raise LLMResponseError("LLM returned an invalid completion response.") from error
        if not choices:
            raise LLMResponseError("LLM returned no completion choices.")

        try:
            content = choices[0].message.content
        except Exception as error:
            raise LLMResponseError("LLM returned an invalid completion response.") from error
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
        except json.JSONDecodeError as error:
            raise LLMResponseError(
                "LLM returned invalid JSON for a structured completion."
            ) from error
        if not isinstance(parsed, dict):
            raise LLMResponseError("LLM returned JSON that was not an object.")
        return parsed


def _request_error_message(error: Exception) -> str:
    """Describe a provider failure without copying credentials, prompts, or response text."""
    status_code = _provider_status_code(error)
    if status_code is not None:
        return f"LLM request failed with HTTP status {status_code}."
    return "LLM request failed before a completion was returned."


def _provider_status_code(error: BaseException) -> int | None:
    """Return a trustworthy HTTP status when the compatible SDK exposes one."""
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        return status_code
    return None


def _provider_error_body(error: BaseException) -> str | None:
    """Extract a redacted provider response body for evidence, never for stdout."""
    body = getattr(error, "body", None)
    if body is None:
        response = getattr(error, "response", None)
        body = getattr(response, "text", None)
    if body is None:
        return None
    if isinstance(body, (dict, list)):
        try:
            text = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            text = str(body)
    else:
        text = str(body)
    return _redact_and_truncate(text)


def _redact_and_truncate(text: str, *, limit: int = 4096) -> str:
    """Keep useful error evidence while removing common bearer/query credentials."""
    redacted = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)([^\s,;\"']+)",
        r"\1[REDACTED]",
        text,
    )
    redacted = re.sub(r"(?i)(bearer\s+)([^\s,;\"']+)", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", redacted)
    redacted = re.sub(
        r"(?i)([?&;](?:api[_-]?key|token|key|authorization)=)([^&#\s]+)",
        r"\1[REDACTED]",
        redacted,
    )
    if len(redacted) <= limit:
        return redacted
    return redacted[:limit] + "…[truncated]"


def _strip_json_fence(content: str) -> str:
    """Accept a JSON code fence while keeping malformed text out of errors."""
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped
