from types import SimpleNamespace

import pytest

import app.gpt.client as llm_module
from app.gpt import (
    DEFAULT_AUGMENT_MODEL,
    DEFAULT_LLM_BASE_URL,
    DEFAULT_OPENAI_MODEL,
    EvalSettings,
    LLMClient,
    LLMConfigurationError,
    LLMRequestError,
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


def test_openrouter_preset_uses_provider_key() -> None:
    settings = LLMSettings.from_env({"OPENROUTER_API_KEY": "provider-secret"})

    assert settings.provider == "openrouter"
    assert settings.api_key == "provider-secret"
    assert settings.base_url == DEFAULT_LLM_BASE_URL
    assert settings.model == DEFAULT_AUGMENT_MODEL


def test_openai_preset_resolves_discovered_luna_base() -> None:
    settings = LLMSettings.from_env(
        {"VF_LLM_PROVIDER": "openai", "OPENAI_API_KEY": "openai-secret"}
    )

    assert settings.provider == "openai"
    assert settings.model == DEFAULT_OPENAI_MODEL == "gpt-5.6-luna"
    assert settings.base_url == "https://api.openai.com/v1"


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "GPT-5.6-TERRA-xhigh"])
def test_openai_preset_rejects_expensive_tiers_before_client_construction(model: str) -> None:
    with pytest.raises(LLMConfigurationError, match="Sol and Terra"):
        LLMSettings.from_env(
            {
                "VF_LLM_PROVIDER": "openai",
                "OPENAI_API_KEY": "openai-secret",
                "VF_LLM_MODEL": model,
            }
        )


def test_generic_overrides_win_over_provider_preset() -> None:
    settings = LLMSettings.from_env(
        {
            "VF_LLM_PROVIDER": "openrouter",
            "OPENROUTER_API_KEY": "provider-key",
            "VF_LLM_API_KEY": "override-key",
            "VF_LLM_BASE_URL": "https://override.example/v1",
            "VF_LLM_MODEL": "provider/override-model",
            "VF_AUGMENT_MODEL": "provider/legacy-model",
        }
    )

    assert settings.api_key == "override-key"
    assert settings.base_url == "https://override.example/v1"
    assert settings.model == "provider/override-model"


def test_eval_settings_require_explicit_eval_variables_without_llm_fallback() -> None:
    with pytest.raises(LLMConfigurationError, match="VF_EVAL_BASE_URL"):
        EvalSettings.from_env(
            {
                "VF_LLM_API_KEY": "openrouter-key",
                "VF_LLM_BASE_URL": "https://openrouter.example/v1",
                "VF_AUGMENT_MODEL": "provider/augmentation-model",
            }
        )

    settings = EvalSettings.from_env(
        {
            "VF_EVAL_BASE_URL": "http://127.0.0.1:8000/v1",
            "VF_EVAL_MODEL": "Qwen2.5-1.5B-Instruct",
        }
    )

    assert settings.base_url == "http://127.0.0.1:8000/v1"
    assert settings.model == "Qwen2.5-1.5B-Instruct"
    assert settings.api_key == "vf-local-eval"


def test_eval_settings_can_use_a_distinct_optional_eval_key() -> None:
    settings = EvalSettings.from_env(
        {
            "VF_EVAL_BASE_URL": "https://eval.example/v1",
            "VF_EVAL_MODEL": "provider/eval-model",
            "VF_EVAL_API_KEY": "eval-only-key",
            "VF_LLM_API_KEY": "must-not-be-selected",
        }
    )

    assert settings.api_key == "eval-only-key"


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
    assert "configured base_url" in str(raised.value)


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

    with pytest.raises(LLMRequestError) as raised:
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
    with pytest.raises(LLMRequestError) as raised:
        client.complete([{"role": "user", "content": "Hello"}])

    assert str(raised.value) == "LLM request failed with HTTP status 429."
    assert secret not in str(raised.value)


def test_client_preserves_redacted_provider_body_and_exception_cause() -> None:
    secret = "sk-never-store-this"

    class FakeHTTPError(RuntimeError):
        def __init__(self) -> None:
            super().__init__("provider is unavailable")
            self.status_code = 503
            self.body = {"detail": f"Authorization: Bearer {secret}"}

    class FakeCompletions:
        def create(self, **kwargs):
            del kwargs
            raise FakeHTTPError()

    client = LLMClient(
        LLMSettings(api_key="test-key"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )

    with pytest.raises(LLMRequestError) as raised:
        client.complete([{"role": "user", "content": "Hello"}])

    assert raised.value.status_code == 503
    assert raised.value.provider_body is not None
    assert secret not in raised.value.provider_body
    assert "[REDACTED]" in raised.value.provider_body
    assert isinstance(raised.value.__cause__, FakeHTTPError)


def test_client_caps_provider_body_evidence() -> None:
    class FakeHTTPError(RuntimeError):
        status_code = 502
        body = "x" * 5000

    class FakeCompletions:
        def create(self, **kwargs):
            del kwargs
            raise FakeHTTPError("gateway failure")

    client = LLMClient(
        LLMSettings(api_key="test-key"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )

    with pytest.raises(LLMRequestError) as raised:
        client.complete([{"role": "user", "content": "Hello"}])

    assert raised.value.provider_body is not None
    assert len(raised.value.provider_body) <= 4110
    assert raised.value.provider_body.endswith("…[truncated]")


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


def test_client_returns_structured_tool_turn_and_usage() -> None:
    requests: list[dict[str, object]] = []

    class FakeCompletions:
        def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(
                model="provider/tool-model",
                usage=SimpleNamespace(
                    prompt_tokens=11,
                    completion_tokens=7,
                    total_tokens=18,
                    cost=0.001,
                ),
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        message=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    id="call-1",
                                    function=SimpleNamespace(
                                        name="analyze_traffic",
                                        arguments='{"cluster_id":"data-pull-sql"}',
                                    ),
                                )
                            ],
                        ),
                    )
                ],
            )

    client = LLMClient(
        LLMSettings(api_key="test-key", model="provider/tool-model"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )
    turn = client.tool_turn(
        [{"role": "user", "content": "Analyze"}],
        tools=[{"type": "function", "function": {"name": "analyze_traffic"}}],
        max_completion_tokens=50,
        timeout=4,
    )

    assert turn.tool_calls[0].name == "analyze_traffic"
    assert turn.usage.total_tokens == 18
    assert turn.usage.provider_reported_cost_usd == 0.001
    assert turn.finish_reason == "tool_calls"
    assert requests[0]["parallel_tool_calls"] is False
    assert requests[0]["max_completion_tokens"] == 50
    assert requests[0]["timeout"] == 4


def test_runtime_openai_model_override_still_rejects_sol_before_request() -> None:
    requests: list[dict[str, object]] = []
    client = LLMClient(
        LLMSettings(
            api_key="test-key",
            provider="openai",
            model="gpt-5.6-luna",
            base_url="https://api.openai.com/v1",
        ),
        client=SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: requests.append(kwargs))
            )
        ),
    )

    with pytest.raises(LLMConfigurationError, match="Sol and Terra"):
        client.complete([{"role": "user", "content": "Hello"}], model="gpt-5.6-sol")

    assert requests == []


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
