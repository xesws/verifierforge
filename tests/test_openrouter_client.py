from types import SimpleNamespace

import pytest

import app.gpt.openrouter as openrouter
from app.gpt.openrouter import (
    DEFAULT_MODEL,
    OPENROUTER_BASE_URL,
    OpenRouterClient,
    OpenRouterConfigurationError,
    OpenRouterResponseError,
    OpenRouterSettings,
)


def test_settings_read_openrouter_environment() -> None:
    settings = OpenRouterSettings.from_env(
        {
            "OPENROUTER_API_KEY": "sk-or-test",
            "VF_GPT_MODEL": "openai/test-model",
            "VF_APP_URL": "https://verifierforge.example",
            "VF_APP_TITLE": "VerifierForge Test",
        }
    )

    assert settings.model == "openai/test-model"
    assert settings.base_url == OPENROUTER_BASE_URL
    assert settings.headers() == {
        "HTTP-Referer": "https://verifierforge.example",
        "X-OpenRouter-Title": "VerifierForge Test",
    }


def test_settings_require_openrouter_key() -> None:
    with pytest.raises(OpenRouterConfigurationError, match="OPENROUTER_API_KEY"):
        OpenRouterSettings.from_env({})


def test_settings_default_blank_model_to_explicit_grok_model() -> None:
    settings = OpenRouterSettings.from_env(
        {"OPENROUTER_API_KEY": "sk-or-test", "VF_GPT_MODEL": "   "}
    )

    assert settings.model == DEFAULT_MODEL


def test_client_targets_openrouter_with_default_model(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(openrouter, "OpenAI", FakeOpenAI)
    settings = OpenRouterSettings(api_key="sk-or-test")
    OpenRouterClient(settings)

    assert captured == {
        "api_key": "sk-or-test",
        "base_url": OPENROUTER_BASE_URL,
        "default_headers": {"X-OpenRouter-Title": "VerifierForge"},
    }
    assert settings.model == DEFAULT_MODEL


def test_client_extracts_completion_and_uses_selected_model() -> None:
    requests: list[dict[str, object]] = []

    class FakeCompletions:
        def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Verifier code"))]
            )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    client = OpenRouterClient(
        OpenRouterSettings(api_key="sk-or-test", model="openai/default"),
        client=fake_client,
    )

    assert client.complete(
        [{"role": "user", "content": "Write a verifier"}],
        model="openai/pinned",
        temperature=0.2,
    ) == "Verifier code"
    assert requests == [
        {
            "model": "openai/pinned",
            "messages": [{"role": "user", "content": "Write a verifier"}],
            "temperature": 0.2,
        }
    ]


def test_client_rejects_empty_completion() -> None:
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=""))]
                )
            )
        )
    )
    client = OpenRouterClient(
        OpenRouterSettings(api_key="sk-or-test"), client=fake_client
    )

    with pytest.raises(OpenRouterResponseError, match="empty completion"):
        client.complete([{"role": "user", "content": "Hello"}])
