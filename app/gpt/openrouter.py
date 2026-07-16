"""Deprecated OpenRouter-named imports for callers migrating to :mod:`app.gpt`."""

from .client import (
    DEFAULT_AUGMENT_MODEL,
    DEFAULT_LLM_BASE_URL,
    LLMClient,
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseError,
    LLMSettings,
)


# These aliases preserve import compatibility only.  Their configuration is
# canonical VF_LLM_* / VF_AUGMENT_MODEL; legacy OPENROUTER_* variables are ignored.
OPENROUTER_BASE_URL = DEFAULT_LLM_BASE_URL
DEFAULT_MODEL = DEFAULT_AUGMENT_MODEL
OpenRouterClient = LLMClient
OpenRouterConfigurationError = LLMConfigurationError
OpenRouterRequestError = LLMRequestError
OpenRouterResponseError = LLMResponseError
OpenRouterSettings = LLMSettings


__all__ = [
    "DEFAULT_MODEL",
    "OPENROUTER_BASE_URL",
    "OpenRouterClient",
    "OpenRouterConfigurationError",
    "OpenRouterRequestError",
    "OpenRouterResponseError",
    "OpenRouterSettings",
]
