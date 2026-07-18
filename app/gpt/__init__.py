"""OpenAI-compatible LLM runtime integrations for VerifierForge."""

from .client import (
    DEFAULT_AUGMENT_MODEL,
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_OPENAI_MODEL,
    EvalSettings,
    LLMClient,
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseError,
    LLMSettings,
    LLMToolCall,
    LLMTurn,
    LLMUsage,
    OPENAI_BASE_URL,
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
    "DEFAULT_LLM_PROVIDER",
    "DEFAULT_OPENAI_MODEL",
    "EvalSettings",
    "LLMClient",
    "LLMConfigurationError",
    "LLMRequestError",
    "LLMResponseError",
    "LLMSettings",
    "LLMToolCall",
    "LLMTurn",
    "LLMUsage",
    "OPENAI_BASE_URL",
    "OpenRouterClient",
    "OpenRouterConfigurationError",
    "OpenRouterResponseError",
    "OpenRouterSettings",
]
