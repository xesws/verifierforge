from __future__ import annotations

import pytest

from core.eval_runner import (
    EvaluationMetrics,
    EvaluationRecordError,
    evaluate_records,
    parse_evaluation_record,
)


SCHEMA = """
CREATE TABLE people (name TEXT NOT NULL);
INSERT INTO people VALUES ('Ada');
"""

RECORD = {
    "id": "ada",
    "prompt": "Return Ada's name as SQL.",
    "schema_sql": SCHEMA,
    "expected_results": [["Ada"]],
}


class SequenceClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.requests: list[tuple[list[dict[str, str]], str | None, float | None]] = []

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        self.requests.append((messages, model, temperature))
        return next(self.responses)


def test_runner_accounts_for_first_pass_any_pass_and_mixed_groups() -> None:
    second_record = {**RECORD, "id": "ada-again"}
    client = SequenceClient(
        [
            "SELECT name FROM people",  # record one: full pass at sample one
            "SELECT name FROM people WHERE name = 'Nobody'",  # then fail
            "SELECT name FROM people WHERE name = 'Nobody'",  # record two: fail first
            "SELECT name FROM people",  # then full pass
        ]
    )

    run = evaluate_records([RECORD, second_record], client, k=2, model="test-model")

    assert run.metrics.baseline_pass_at_1 == 0.5
    assert run.metrics.pass_at_k == 1.0
    assert run.metrics.mixed_fraction == 1.0
    assert [group.full_passes for group in run.groups] == [(True, False), (False, True)]
    assert all(request[1] == "test-model" for request in client.requests)
    assert all(request[2] == 1.0 for request in client.requests)


def test_runner_requires_an_exact_full_verifier_score() -> None:
    # This query returns the right row, but the verifier removes 0.05 for its
    # over-400-character completion.  It must not count as a full pass.
    long_matching_sql = "SELECT name FROM people -- " + ("x" * 401)
    client = SequenceClient([long_matching_sql])

    run = evaluate_records([RECORD], client, k=1)

    assert run.groups[0].scores == (0.95,)
    assert run.metrics.baseline_pass_at_1 == 0.0
    assert run.metrics.pass_at_k == 0.0
    assert run.metrics.mixed_fraction == 0.0


def test_metrics_expose_an_additive_pass_at_8_for_gate_a_runs() -> None:
    metrics = EvaluationMetrics(
        baseline_pass_at_1=0.25,
        pass_at_k=0.75,
        mixed_fraction=0.5,
        record_count=4,
        k=8,
    )

    assert metrics.as_dict()["pass_at_8"] == 0.75
    assert metrics.as_dict()["pass_at_k"] == 0.75
    assert "pass_at_8" not in EvaluationMetrics(
        baseline_pass_at_1=0.25,
        pass_at_k=0.75,
        mixed_fraction=0.5,
        record_count=4,
        k=2,
    ).as_dict()


@pytest.mark.parametrize(
    "record",
    [
        {},
        {"prompt": "question", "schema_sql": SCHEMA, "expected_results": "not rows"},
        {"prompt": "question", "schema_sql": SCHEMA, "expected_results": ["not a row"]},
        {
            "prompt": "question",
            "schema_sql": SCHEMA,
            "expected_results": [[{"unhashable": "cell"}]],
        },
        {"prompt": "question", "schema_sql": SCHEMA, "expected_results": [], "id": 7},
    ],
)
def test_record_validation_rejects_malformed_json_shapes(record: dict[str, object]) -> None:
    with pytest.raises(EvaluationRecordError):
        parse_evaluation_record(record)


@pytest.mark.parametrize(
    "cell",
    [
        b"bytes are not JSON",
        ("tuple",),
        {"object": "is not a scalar"},
        ["list", "is not a scalar"],
        complex(1, 2),
        float("nan"),
        float("inf"),
    ],
)
def test_record_validation_rejects_non_sql_scalar_or_non_finite_cells(cell: object) -> None:
    with pytest.raises(EvaluationRecordError, match="finite SQL scalar JSON value"):
        parse_evaluation_record({**RECORD, "expected_results": [[cell]]})


def test_record_validation_accepts_json_sql_scalars() -> None:
    record = parse_evaluation_record(
        {
            **RECORD,
            "expected_results": [[None, True, False, -3, 1.5, "Ada"]],
        }
    )

    assert record.expected_results == ((None, True, False, -3, 1.5, "Ada"),)


def test_runner_validates_every_record_before_making_any_completion_request() -> None:
    client = SequenceClient(["SELECT name FROM people"])

    with pytest.raises(EvaluationRecordError):
        evaluate_records(
            [
                RECORD,
                {
                    "prompt": "unhashable expected result",
                    "schema_sql": SCHEMA,
                    "expected_results": [[{"unhashable": "cell"}]],
                },
            ],
            client,
            k=1,
        )

    assert client.requests == []


def test_runner_preserves_sample_slots_with_bounded_workers() -> None:
    class PerPromptClient:
        def complete(self, messages, *, model=None, temperature=None) -> str:
            del model, temperature
            prompt = messages[0]["content"]
            if "one" in prompt:
                return "SELECT name FROM people"
            return "SELECT name FROM people WHERE name = 'Nobody'"

    first_record = {**RECORD, "id": "one", "prompt": "case one"}
    second_record = {**RECORD, "id": "two", "prompt": "case two"}

    run = evaluate_records(
        [first_record, second_record], PerPromptClient(), k=2, workers=4
    )

    assert [group.full_passes for group in run.groups] == [(True, True), (False, False)]
    assert run.metrics.baseline_pass_at_1 == 0.5
    assert run.metrics.pass_at_k == 0.5
    assert run.metrics.mixed_fraction == 0.0


@pytest.mark.parametrize("k", [0, -1, True])
def test_runner_rejects_non_positive_sample_counts(k: int) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        evaluate_records([RECORD], SequenceClient([]), k=k)


@pytest.mark.parametrize("workers", [0, -1, True])
def test_runner_rejects_non_positive_worker_counts(workers: int) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        evaluate_records([RECORD], SequenceClient([]), workers=workers)
