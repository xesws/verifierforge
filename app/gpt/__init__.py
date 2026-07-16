"""OpenAI-compatible LLM runtime integrations for VerifierForge."""

from .client import (
    DEFAULT_AUGMENT_MODEL,
    DEFAULT_LLM_BASE_URL,
    LLMClient,
    LLMConfigurationError,
    LLMResponseError,
    LLMSettings,
)
from .openrouter import (
    OpenRouterClient,
    OpenRouterConfigurationError,
    OpenRouterResponseError,
    OpenRouterSettings,
)

__all__ = [
    "DEFAULT_AUGMENT_MODEL",
    "DEFAULT_LLM_BASE_URL",
    "LLMClient",
    "LLMConfigurationError",
    "LLMResponseError",
    "LLMSettings",
    "OpenRouterClient",
    "OpenRouterConfigurationError",
    "OpenRouterResponseError",
    "OpenRouterSettings",
]
