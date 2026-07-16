from types import SimpleNamespace

import pytest

import app.gpt.client as llm_module
from app.gpt import (
    DEFAULT_AUGMENT_MODEL,
    DEFAULT_LLM_BASE_URL,
    LLMClient,
    LLMConfigurationError,
    LLMResponseError,
    LLMSettings,
)
from app.gpt.openrouter import OpenRouterSettings


def test_settings_read_only_canonical_environment() -> None:
    settings = LLMSettings.from_env(
        {
            "VF_LLM_API_KEY": "canonical-key",
            "VF_LLM_BASE_URL": "https://llm.example/v1",
            "VF_AUGMENT_MODEL": "provider/test-model",
            "OPENROUTER_API_KEY": "legacy-key",
            "VF_GPT_MODEL": "legacy/model",
        }
    )

    assert settings.api_key == "canonical-key"
    assert settings.base_url == "https://llm.example/v1"
    assert settings.model == "provider/test-model"


def test_settings_require_canonical_key_and_ignore_legacy_key() -> None:
    with pytest.raises(LLMConfigurationError, match="VF_LLM_API_KEY") as raised:
        LLMSettings.from_env({"OPENROUTER_API_KEY": "legacy-secret"})

    assert "legacy-secret" not in str(raised.value)


def test_blank_optional_environment_values_use_generic_defaults() -> None:
    settings = LLMSettings.from_env(
        {
            "VF_LLM_API_KEY": "test-key",
            "VF_LLM_BASE_URL": "   ",
            "VF_AUGMENT_MODEL": "   ",
        }
    )

    assert settings.base_url == DEFAULT_LLM_BASE_URL
    assert settings.model == DEFAULT_AUGMENT_MODEL


def test_settings_loads_local_dotenv_without_overriding_shell(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text(
        "VF_LLM_API_KEY=dotenv-key\n"
        "VF_LLM_BASE_URL=https://dotenv.example/v1\n"
        "VF_AUGMENT_MODEL=provider/dotenv-model\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VF_LLM_API_KEY", "shell-key")
    monkeypatch.delenv("VF_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("VF_AUGMENT_MODEL", raising=False)

    settings = LLMSettings.from_env()

    assert settings.api_key == "shell-key"
    assert settings.base_url == "https://dotenv.example/v1"
    assert settings.model == "provider/dotenv-model"


def test_settings_hide_api_key_in_repr() -> None:
    settings = LLMSettings(api_key="very-secret-key")

    assert "very-secret-key" not in repr(settings)


def test_settings_can_load_an_explicit_repo_dotenv_path_without_overriding_env(
    tmp_path, monkeypatch
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "VF_LLM_API_KEY=dotenv-key\nVF_AUGMENT_MODEL=dotenv/model\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VF_LLM_API_KEY", "shell-key")
    # The developer shell may already define this optional setting. Remove it
    # through monkeypatch so the test can prove explicit dotenv loading while
    # pytest restores the original process environment afterward.
    monkeypatch.delenv("VF_AUGMENT_MODEL", raising=False)

    settings = LLMSettings.from_env(dotenv_path=dotenv_path)

    assert settings.api_key == "shell-key"
    assert settings.model == "dotenv/model"


def test_client_uses_openai_base_url_override_without_provider_headers(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(llm_module, "OpenAI", FakeOpenAI)
    LLMClient(
        LLMSettings(
            api_key="test-key",
            base_url="https://compatible.example/v1",
            model="provider/model",
        )
    )

    assert captured == {
        "api_key": "test-key",
        "base_url": "https://compatible.example/v1",
    }


def test_client_redacts_initialization_failure(monkeypatch) -> None:
    secret = "sk-should-not-appear"

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            raise ValueError(f"invalid endpoint with key {kwargs['api_key']}")

    monkeypatch.setattr(llm_module, "OpenAI", FakeOpenAI)
    with pytest.raises(LLMConfigurationError) as raised:
        LLMClient(LLMSettings(api_key=secret))

    assert secret not in str(raised.value)
    assert secret not in repr(raised.value)
    assert "VF_LLM_BASE_URL" in str(raised.value)


def test_client_uses_configured_model_by_default() -> None:
    requests: list[dict[str, object]] = []

    class FakeCompletions:
        def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Verifier code"))]
            )

    client = LLMClient(
        LLMSettings(api_key="test-key", model="provider/configured-model"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )

    assert client.model == "provider/configured-model"
    assert client.complete(
        [{"role": "user", "content": "Write a verifier"}], temperature=0.2
    ) == "Verifier code"
    assert requests == [
        {
            "model": "provider/configured-model",
            "messages": [{"role": "user", "content": "Write a verifier"}],
            "temperature": 0.2,
        }
    ]


def test_client_allows_a_runtime_model_override() -> None:
    requests: list[dict[str, object]] = []

    class FakeCompletions:
        def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
            )

    client = LLMClient(
        LLMSettings(api_key="test-key", model="provider/default-model"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )
    client.complete(
        [{"role": "user", "content": "Write a verifier"}],
        model="provider/runtime-override",
    )

    assert requests[0]["model"] == "provider/runtime-override"


def test_client_redacts_provider_error_text() -> None:
    secret = "sk-should-not-appear"

    class FakeCompletions:
        def create(self, **kwargs):
            raise RuntimeError(f"authorization failed: {secret}")

    client = LLMClient(
        LLMSettings(api_key=secret),
        client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )

    with pytest.raises(LLMResponseError) as raised:
        client.complete([{"role": "user", "content": "Hello"}])

    assert secret not in str(raised.value)
    assert secret not in repr(raised.value)
    assert str(raised.value) == "LLM request failed before a completion was returned."


def test_client_reports_http_status_without_provider_error_text() -> None:
    secret = "sk-should-not-appear"

    class FakeHTTPError(RuntimeError):
        status_code = 429

    class FakeCompletions:
        def create(self, **kwargs):
            raise FakeHTTPError(f"rate limited for {secret}")

    client = LLMClient(
        LLMSettings(api_key=secret),
        client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )
    with pytest.raises(LLMResponseError) as raised:
        client.complete([{"role": "user", "content": "Hello"}])

    assert str(raised.value) == "LLM request failed with HTTP status 429."
    assert secret not in str(raised.value)


def test_client_extracts_fenced_json_and_requests_json_mode() -> None:
    requests: list[dict[str, object]] = []

    class FakeCompletions:
        def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="```json\n{\"ok\": true}\n```")
                    )
                ]
            )

    client = LLMClient(
        LLMSettings(api_key="test-key"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )

    assert client.complete_json([{"role": "user", "content": "Return JSON"}]) == {"ok": True}
    assert requests[0]["response_format"] == {"type": "json_object"}


def test_client_rejects_empty_or_invalid_structured_completions() -> None:
    empty_client = LLMClient(
        LLMSettings(api_key="test-key"),
        client=SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_: SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content=""))]
                    )
                )
            )
        ),
    )
    with pytest.raises(LLMResponseError, match="empty completion"):
        empty_client.complete([{"role": "user", "content": "Hello"}])

    invalid_json_client = LLMClient(
        LLMSettings(api_key="test-key"),
        client=SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_: SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))]
                    )
                )
            )
        ),
    )
    with pytest.raises(LLMResponseError, match="invalid JSON"):
        invalid_json_client.complete_json([{"role": "user", "content": "Return JSON"}])


def test_openrouter_alias_uses_canonical_configuration() -> None:
    settings = OpenRouterSettings.from_env({"VF_LLM_API_KEY": "test-key"})

    assert isinstance(settings, LLMSettings)
    assert settings.model == DEFAULT_AUGMENT_MODEL
