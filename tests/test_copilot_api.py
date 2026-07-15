from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.api.copilot import (
    DEFAULT_MODEL,
    ProposalRequest,
    VerifierCopilot,
    get_copilot,
    get_sandbox,
)
from app.api.main import app
from app.sandbox import SandboxResult


class FakeStructuredClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, messages, *, model=None, temperature=0.2):
        self.calls.append(
            {"messages": messages, "model": model, "temperature": temperature}
        )
        return self.responses.pop(0)


def _batch(start: int, count: int, *, design: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "cases": [
            {
                "case_id": f"case-{index}",
                "prompt": f"Question {index}",
                "expected_sql": "SELECT 1",
                "expected_results": [[1]],
            }
            for index in range(start, start + count)
        ]
    }
    if design:
        payload.update(
            {
                "verifier_code": "class ProposedVerifier: pass",
                "test_code": "def test_proposed_verifier(): pass",
                "tiers": {"0.2": "parses", "1.0": "matches"},
            }
        )
    return payload


def test_copilot_batches_cases_with_explicit_grok_model() -> None:
    client = FakeStructuredClient([_batch(1, 10, design=True), _batch(11, 2, design=False)])
    copilot = VerifierCopilot(client)

    proposal = copilot.propose(
        ProposalRequest(task="answer SQL", schema_sql="CREATE TABLE things (id INTEGER)", seed_count=12)
    )

    assert proposal.model == DEFAULT_MODEL
    assert len(proposal.cases) == 12
    assert proposal.review_required is True
    assert [call["model"] for call in client.calls] == [DEFAULT_MODEL, DEFAULT_MODEL]
    assert "exactly 10" in client.calls[0]["messages"][1]["content"]
    assert "exactly 2" in client.calls[1]["messages"][1]["content"]


def test_copilot_makes_one_repair_attempt_for_invalid_batch() -> None:
    client = FakeStructuredClient([
        {"cases": []},
        _batch(1, 1, design=True),
    ])

    proposal = VerifierCopilot(client).propose(
        ProposalRequest(task="answer SQL", schema_sql="CREATE TABLE things (id INTEGER)", seed_count=1)
    )

    assert proposal.cases[0].case_id == "case-1"
    assert len(client.calls) == 2
    assert "previous response was unusable" in client.calls[1]["messages"][1]["content"]


def test_copilot_lifts_first_batch_design_fields_from_a_case() -> None:
    nested = _batch(1, 1, design=True)
    nested["cases"][0].update(
        {
            "verifier_code": nested.pop("verifier_code"),
            "test_code": nested.pop("test_code"),
            "tiers": nested.pop("tiers"),
        }
    )

    proposal = VerifierCopilot(FakeStructuredClient([nested])).propose(
        ProposalRequest(task="answer SQL", schema_sql="CREATE TABLE things (id INTEGER)", seed_count=1)
    )

    assert proposal.verifier_code == "class ProposedVerifier: pass"


def test_proposal_route_uses_injected_copilot() -> None:
    fake = FakeStructuredClient([_batch(1, 1, design=True)])
    app.dependency_overrides[get_copilot] = lambda: VerifierCopilot(fake)
    try:
        response = TestClient(app).post(
            "/copilot/nl2sql/proposals",
            json={
                "task": "answer SQL",
                "schema_sql": "CREATE TABLE things (id INTEGER)",
                "seed_count": 1,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["model"] == DEFAULT_MODEL
    assert response.json()["cases"][0]["case_id"] == "case-1"


def test_validate_route_uses_injected_docker_sandbox() -> None:
    class FakeSandbox:
        def validate(self, candidate_code: str) -> SandboxResult:
            assert candidate_code == "print('safe')"
            return SandboxResult(
                passed=True,
                stdout="safe\n",
                stderr="",
                duration_seconds=0.01,
                timed_out=False,
                returncode=0,
            )

    app.dependency_overrides[get_sandbox] = FakeSandbox
    try:
        response = TestClient(app).post(
            "/copilot/nl2sql/validate", json={"candidate_code": "print('safe')"}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "passed": True,
        "stdout": "safe\n",
        "stderr": "",
        "duration_seconds": 0.01,
        "timed_out": False,
    }
