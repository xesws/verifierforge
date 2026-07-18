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


DEFAULT_LLM_PROVIDER = "openrouter"
DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_AUGMENT_MODEL = "z-ai/glm-5.2"
OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
FORBIDDEN_OPENAI_MODEL_MARKERS = ("sol", "terra")


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
    model: str = ""
    base_url: str = ""
    provider: str = DEFAULT_LLM_PROVIDER

    def __post_init__(self) -> None:
        provider = self.provider.strip().lower() or DEFAULT_LLM_PROVIDER
        if provider not in {"openrouter", "openai"}:
            raise LLMConfigurationError(
                "VF_LLM_PROVIDER must be either openrouter or openai."
            )
        api_key = self.api_key.strip()
        if not api_key:
            raise LLMConfigurationError(
                f"A key must be configured for the {provider} LLM provider."
            )
        model = self.model.strip() or _provider_default_model(provider)
        _validate_model_policy(provider, model)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "model", model)
        object.__setattr__(
            self,
            "base_url",
            self.base_url.strip() or _provider_default_base_url(provider),
        )

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
        provider = values.get("VF_LLM_PROVIDER", DEFAULT_LLM_PROVIDER).strip().lower()
        if provider not in {"openrouter", "openai"}:
            raise LLMConfigurationError(
                "VF_LLM_PROVIDER must be either openrouter or openai."
            )
        provider_key_name = (
            "OPENROUTER_API_KEY" if provider == "openrouter" else "OPENAI_API_KEY"
        )
        explicit_model = values.get("VF_LLM_MODEL", "").strip()
        legacy_model = values.get("VF_AUGMENT_MODEL", "").strip()
        return cls(
            api_key=values.get("VF_LLM_API_KEY", "").strip()
            or values.get(provider_key_name, ""),
            model=explicit_model or legacy_model or _provider_default_model(provider),
            base_url=values.get("VF_LLM_BASE_URL", "").strip()
            or _provider_default_base_url(provider),
            provider=provider,
        )


@dataclass(frozen=True)
class LLMUsage:
    """Token accounting returned by an OpenAI-compatible provider."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    provider_reported_cost_usd: float | None = None


@dataclass(frozen=True)
class LLMToolCall:
    """One structured function request emitted by the model."""

    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class LLMTurn:
    """One assistant turn, including tool requests and auditable usage."""

    content: str | None
    tool_calls: tuple[LLMToolCall, ...]
    usage: LLMUsage
    model: str
    finish_reason: str | None


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
        max_completion_tokens: int | None = None,
        timeout: float | None = None,
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
        if max_completion_tokens is not None:
            request["max_completion_tokens"] = max_completion_tokens
        if timeout is not None:
            request["timeout"] = timeout

        response = self._create(request)

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

    def tool_turn(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]],
        tool_choice: str | Mapping[str, Any] = "auto",
        max_completion_tokens: int = 1024,
        timeout: float = 30.0,
        parallel_tool_calls: bool = False,
        model: str | None = None,
    ) -> LLMTurn:
        """Return one structured assistant/tool turn without interpreting it."""
        resolved_model = model or self.model
        _validate_model_policy(getattr(self.settings, "provider", "openrouter"), resolved_model)
        request: dict[str, Any] = {
            "model": resolved_model,
            "messages": [dict(message) for message in messages],
            "tools": [dict(tool) for tool in tools],
            "tool_choice": tool_choice,
            "parallel_tool_calls": parallel_tool_calls,
            "max_completion_tokens": max_completion_tokens,
            "timeout": timeout,
        }
        response = self._create(request)
        try:
            choice = response.choices[0]
            message = choice.message
        except Exception as error:
            raise LLMResponseError("LLM returned an invalid tool response.") from error

        calls: list[LLMToolCall] = []
        for call in getattr(message, "tool_calls", None) or ():
            try:
                calls.append(
                    LLMToolCall(
                        call_id=str(call.id),
                        name=str(call.function.name),
                        arguments=str(call.function.arguments),
                    )
                )
            except Exception as error:
                raise LLMResponseError("LLM returned an invalid tool call.") from error
        content_value = getattr(message, "content", None)
        content = content_value if isinstance(content_value, str) and content_value else None
        if not calls and content is None:
            raise LLMResponseError("LLM returned neither content nor a tool call.")
        return LLMTurn(
            content=content,
            tool_calls=tuple(calls),
            usage=_usage_from_response(response),
            model=str(getattr(response, "model", resolved_model) or resolved_model),
            finish_reason=(
                str(choice.finish_reason)
                if getattr(choice, "finish_reason", None) is not None
                else None
            ),
        )

    def chat_turn(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        max_completion_tokens: int = 32,
        timeout: float = 30.0,
        model: str | None = None,
    ) -> LLMTurn:
        """Return one ordinary chat turn with the same usage envelope as tools."""
        resolved_model = model or self.model
        response = self._create(
            {
                "model": resolved_model,
                "messages": [dict(message) for message in messages],
                "max_completion_tokens": max_completion_tokens,
                "timeout": timeout,
            }
        )
        try:
            choice = response.choices[0]
            message = choice.message
            content_value = message.content
        except Exception as error:
            raise LLMResponseError("LLM returned an invalid completion response.") from error
        if not isinstance(content_value, str) or not content_value.strip():
            raise LLMResponseError("LLM returned an empty completion.")
        return LLMTurn(
            content=content_value,
            tool_calls=(),
            usage=_usage_from_response(response),
            model=str(getattr(response, "model", resolved_model) or resolved_model),
            finish_reason=(
                str(choice.finish_reason)
                if getattr(choice, "finish_reason", None) is not None
                else None
            ),
        )

    def _create(self, request: Mapping[str, Any]) -> Any:
        resolved_model = str(request.get("model", self.model))
        _validate_model_policy(getattr(self.settings, "provider", "openrouter"), resolved_model)
        try:
            return self._client.chat.completions.create(**dict(request))
        except Exception as error:
            raise LLMRequestError(
                _request_error_message(error),
                status_code=_provider_status_code(error),
                provider_body=_provider_error_body(error),
            ) from error

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


def _provider_default_model(provider: str) -> str:
    return DEFAULT_OPENAI_MODEL if provider == "openai" else DEFAULT_AUGMENT_MODEL


def _provider_default_base_url(provider: str) -> str:
    return OPENAI_BASE_URL if provider == "openai" else DEFAULT_LLM_BASE_URL


def _validate_model_policy(provider: str, model: str) -> None:
    if provider != "openai":
        return
    lowered = model.lower()
    if any(marker in lowered for marker in FORBIDDEN_OPENAI_MODEL_MARKERS):
        raise LLMConfigurationError(
            "OpenAI Sol and Terra model tiers are forbidden for this work."
        )


def _usage_from_response(response: Any) -> LLMUsage:
    usage = getattr(response, "usage", None)
    input_tokens = _nonnegative_int(getattr(usage, "prompt_tokens", 0))
    output_tokens = _nonnegative_int(getattr(usage, "completion_tokens", 0))
    total_tokens = _nonnegative_int(
        getattr(usage, "total_tokens", input_tokens + output_tokens)
    )
    cost = getattr(usage, "cost", None)
    reported_cost = (
        float(cost)
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0
        else None
    )
    return LLMUsage(input_tokens, output_tokens, total_tokens, reported_cost)


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


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
