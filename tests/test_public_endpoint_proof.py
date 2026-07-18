from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.public_endpoint_proof import EndpointProofError, run_proof


def _client(models: list[str], completion: str = "SELECT name FROM users"):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=completion))],
        usage=None,
    )
    completions = SimpleNamespace(create=lambda **_kwargs: response)
    return SimpleNamespace(
        models=SimpleNamespace(
            list=lambda: SimpleNamespace(data=[SimpleNamespace(id=model) for model in models])
        ),
        chat=SimpleNamespace(completions=completions),
    )


def test_proof_discovers_the_only_served_model() -> None:
    assert run_proof(_client(["served-model"])) == {
        "completion": "SELECT name FROM users",
        "model": "served-model",
        "usage": None,
    }


def test_proof_requires_an_explicit_returned_model_when_ambiguous() -> None:
    client = _client(["a", "b"])
    with pytest.raises(EndpointProofError, match="VF_ENDPOINT_MODEL is required"):
        run_proof(client)
    with pytest.raises(EndpointProofError, match="was not returned"):
        run_proof(client, preferred_model="c")
