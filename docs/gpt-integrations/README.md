# GPT Integrations Documentation

This area owns `app/gpt/`, model-provider configuration, prompt interfaces, model selection, retries, and runtime observability for Verifier Copilot, task expansion, and report narrative generation.

Before changing a provider or prompt path, create a versioned document here and link it from `docs/versions/`. Record the provider endpoint, required environment variables, selected model strategy, headers, error behavior, secret handling, and a network-free test plan. Do not put keys, account identifiers, credit balances, or raw user prompts in committed documentation.

Provider changes must preserve the product's GPT runtime role and identify whether they affect Verifier Copilot, task expansion, or report narrative callers. A provider adapter alone does not authorize changing those product flows; each caller integration receives its own version document.
