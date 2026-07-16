import json
import os
from pathlib import Path

import pytest

import core.nl2sql_augmentation as augmentation
from core.nl2sql_augmentation import (
    AugmentationSummary,
    AugmentationInputError,
    SeedCase,
    augment_seed_cases,
    load_seed_cases,
    write_candidates_jsonl_atomic,
    write_summary_json_atomic,
)


SCHEMA = """
CREATE TABLE employees (name TEXT NOT NULL, active INTEGER NOT NULL);
INSERT INTO employees VALUES ('Ada', 1), ('Grace', 1), ('Linus', 0);
""".strip()


class FakeJSONClient:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.requests = []

    def complete_json(self, messages, *, model=None, temperature=0.2):
        self.requests.append(
            {"messages": messages, "model": model, "temperature": temperature}
        )
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response


def _seed(seed_id="seed-a"):
    return SeedCase(
        seed_id=seed_id,
        question="Which active employees are listed alphabetically?",
        prompt="Return a read-only SQLite query for active employee names.",
        schema_sql=SCHEMA,
        expected_results=[["Ada"], ["Grace"]],
        reference_sql="SELECT name FROM employees WHERE active = 1 ORDER BY name",
    )


def _variant(*, question, prompt, sql, expected_results):
    return {
        "question": question,
        "prompt": prompt,
        "reference_sql": sql,
        "expected_results": expected_results,
    }


def test_augmentation_accepts_only_full_verifier_matches_and_preserves_seed_results():
    client = FakeJSONClient(
        [
            {
                "variants": [
                    _variant(
                        question="Name active staff in alphabetical order.",
                        prompt="Write one read-only SQLite SELECT for active staff names.",
                        sql="SELECT name FROM employees WHERE active = 1 ORDER BY name",
                        expected_results=[["Ada"], ["Grace"]],
                    ),
                    _variant(
                        question="Name active staff in alphabetical order.",
                        prompt="This SQL has the wrong answer.",
                        sql="SELECT name FROM employees WHERE active = 0",
                        expected_results=[["Ada"], ["Grace"]],
                    ),
                    _variant(
                        question="Name active staff in alphabetical order.",
                        prompt="This response changes expected rows.",
                        sql="SELECT name FROM employees WHERE active = 1 ORDER BY name",
                        expected_results=[["Ada"]],
                    ),
                ]
            }
        ]
    )

    candidates, summary = augment_seed_cases(
        seeds=[_seed()], client=client, variants_per_seed=3, model="test-model"
    )

    assert candidates == [
        {
            "id": "aug-seed-a-001",
            "seed_id": "seed-a",
            "question": "Name active staff in alphabetical order.",
            "prompt": "Write one read-only SQLite SELECT for active staff names.",
            "schema_sql": SCHEMA,
            "expected_results": [["Ada"], ["Grace"]],
            "reference_sql": "SELECT name FROM employees WHERE active = 1 ORDER BY name",
        }
    ]
    assert summary.as_dict() == {
        "seed_count": 1,
        "variants_per_seed": 3,
        "proposed_count": 3,
        "accepted_count": 1,
        "rejected_shape_count": 0,
        "rejected_expected_results_count": 1,
        "rejected_verifier_count": 1,
        "duplicate_count": 0,
        "discarded_excess_count": 0,
        "malformed_response_count": 0,
        "retry_count": 0,
        "processed_seed_ids": ["seed-a"],
        "processed_seed_count": 1,
        "unprocessed_seed_ids": [],
        "unprocessed_seed_count": 0,
        "processed_requested_slot_count": 3,
        "total_requested_slot_count": 3,
        "main_yield": 1 / 3,
        "reference_yield": 1 / 3,
    }
    assert client.requests[0]["model"] == "test-model"
    assert client.requests[0]["temperature"] == 0.4
    assert "expected_results" in client.requests[0]["messages"][1]["content"]


def test_augmentation_deduplicates_prompt_sql_pairs_across_sorted_seeds():
    duplicate = _variant(
        question="Which active employees are listed alphabetically?",
        prompt="Return active employee names in alphabetical order.",
        sql="SELECT name FROM employees WHERE active = 1 ORDER BY name",
        expected_results=[["Ada"], ["Grace"]],
    )
    client = FakeJSONClient([{"variants": [duplicate]}, {"variants": [duplicate]}])

    candidates, summary = augment_seed_cases(
        seeds=[_seed("seed-b"), _seed("seed-a")], client=client, variants_per_seed=1
    )

    assert [candidate["id"] for candidate in candidates] == ["aug-seed-a-001"]
    assert [candidate["seed_id"] for candidate in candidates] == ["seed-a"]
    assert summary.accepted_count == 1
    assert summary.duplicate_count == 1


@pytest.mark.parametrize(
    "malformed_response",
    [ValueError("not valid structured JSON"), {"variants": "not-a-list"}],
)
def test_augmentation_retries_one_malformed_response_then_accepts_retry(
    malformed_response,
):
    valid_response = {
        "variants": [
            _variant(
                question="Name active employees.",
                prompt="Return active employee names.",
                sql="SELECT name FROM employees WHERE active = 1 ORDER BY name",
                expected_results=[["Ada"], ["Grace"]],
            )
        ]
    }
    client = FakeJSONClient([malformed_response, valid_response])
    candidates, summary = augment_seed_cases(
        seeds=[_seed()], client=client, variants_per_seed=1
    )

    assert [candidate["seed_id"] for candidate in candidates] == ["seed-a"]
    assert summary.malformed_response_count == 1
    assert summary.rejected_shape_count == 1
    assert summary.retry_count == 1
    assert summary.processed_seed_ids == ("seed-a",)
    assert len(client.requests) == 2


def test_augmentation_drops_seed_after_exactly_one_retry_and_keeps_other_seeds():
    valid_response = {
        "variants": [
            _variant(
                question="Name active employees.",
                prompt="Return active employee names.",
                sql="SELECT name FROM employees WHERE active = 1 ORDER BY name",
                expected_results=[["Ada"], ["Grace"]],
            )
        ]
    }
    client = FakeJSONClient(
        [ValueError("not valid structured JSON"), {"variants": "still-bad"}, valid_response]
    )
    candidates, summary = augment_seed_cases(
        seeds=[_seed("seed-a"), _seed("seed-b")],
        client=client,
        variants_per_seed=1,
    )

    assert [candidate["seed_id"] for candidate in candidates] == ["seed-b"]
    assert summary.malformed_response_count == 2
    assert summary.rejected_shape_count == 2
    assert summary.retry_count == 1
    assert summary.processed_seed_ids == ("seed-a", "seed-b")
    assert len(client.requests) == 3

    with pytest.raises(AugmentationInputError, match="seed ids must be unique"):
        augment_seed_cases(
            seeds=[_seed("same"), _seed("same")],
            client=FakeJSONClient([]),
        )


def test_augmentation_propagates_runtime_errors_without_partial_summary():
    with pytest.raises(RuntimeError, match="provider unavailable"):
        augment_seed_cases(
            seeds=[_seed()],
            client=FakeJSONClient([RuntimeError("provider unavailable")]),
            variants_per_seed=1,
        )


@pytest.mark.parametrize("timebox_seconds", [0, -1, float("nan"), "60"])
def test_augmentation_rejects_invalid_timeboxes(timebox_seconds):
    with pytest.raises(ValueError, match="positive finite"):
        augment_seed_cases(
            seeds=[_seed()],
            client=FakeJSONClient([]),
            timebox_seconds=timebox_seconds,
        )


def test_timebox_finishes_started_seed_and_records_unprocessed_ids():
    class FakeClock:
        def __init__(self) -> None:
            self.now = 0.0

        def __call__(self) -> float:
            return self.now

    class DeadlineCrossingClient:
        def __init__(self, clock: FakeClock) -> None:
            self.clock = clock
            self.calls = 0

        def complete_json(self, messages, *, model=None, temperature=0.2):
            del messages, model, temperature
            self.calls += 1
            self.clock.now = 61.0
            if self.calls == 1:
                raise ValueError("malformed response after the deadline")
            return {
                "variants": [
                    _variant(
                        question="Name active employees.",
                        prompt="Return active employee names.",
                        sql="SELECT name FROM employees WHERE active = 1 ORDER BY name",
                        expected_results=[["Ada"], ["Grace"]],
                    )
                ]
            }

    clock = FakeClock()
    client = DeadlineCrossingClient(clock)
    candidates, summary = augment_seed_cases(
        seeds=[_seed("seed-c"), _seed("seed-a"), _seed("seed-b")],
        client=client,
        variants_per_seed=2,
        timebox_seconds=60,
        clock=clock,
    )

    assert [candidate["seed_id"] for candidate in candidates] == ["seed-a"]
    assert client.calls == 2
    assert summary.processed_seed_ids == ("seed-a",)
    assert summary.unprocessed_seed_ids == ("seed-b", "seed-c")
    assert summary.processed_seed_count == 1
    assert summary.unprocessed_seed_count == 2
    assert summary.processed_requested_slot_count == 2
    assert summary.total_requested_slot_count == 6
    assert summary.main_yield == 0.5
    assert summary.reference_yield == pytest.approx(1 / 6)
    assert summary.retry_count == 1
    assert summary.malformed_response_count == 1


def test_v1_prompt_is_host_rendered_from_the_ddl_only_template():
    seed = load_seed_cases(
        Path(__file__).resolve().parents[1] / "trainer" / "data" / "nl2sql_v1.jsonl"
    )[0]
    assert seed.reference_sql is not None
    client = FakeJSONClient(
        [
            {
                "variants": [
                    {
                        "question": "Show every department name in ascending alphabetical order.",
                        "reference_sql": seed.reference_sql,
                        "expected_results": seed.expected_results,
                    }
                ]
            }
        ]
    )

    candidates, summary = augment_seed_cases(
        seeds=[seed], client=client, variants_per_seed=1
    )

    assert summary.accepted_count == 1
    assert candidates[0]["question"] == (
        "Show every department name in ascending alphabetical order."
    )
    assert "Question: Show every department name" in candidates[0]["prompt"]
    assert "INSERT INTO" not in candidates[0]["prompt"].upper()
    assert "INSERT INTO" not in client.requests[0]["messages"][1]["content"].upper()


def test_load_seeds_and_atomically_replace_candidate_output(tmp_path, monkeypatch):
    reviewed_cases = load_seed_cases(
        Path(__file__).resolve().parents[1] / "trainer" / "data" / "nl2sql_v1.jsonl"
    )
    assert len(reviewed_cases) == 50
    assert reviewed_cases[0].seed_id == "v1-001"
    assert reviewed_cases[0].schema_sql

    seed_path = tmp_path / "compatible.jsonl"
    seed_path.write_text(
        json.dumps(
            {
                "id": "external-1",
                "prompt": "Return the active employee names.",
                "schema_sql": SCHEMA,
                "expected_results": [["Ada"], ["Grace"]],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    loaded = load_seed_cases(seed_path)
    assert loaded == [
        SeedCase(
            seed_id="external-1",
            prompt="Return the active employee names.",
            schema_sql=SCHEMA,
            expected_results=[["Ada"], ["Grace"]],
        )
    ]

    output_path = tmp_path / "frozen" / "candidates.jsonl"
    output_path.parent.mkdir()
    output_path.write_text("old-content\n", encoding="utf-8")
    replace_calls = []
    real_replace = os.replace

    def record_replace(source, destination):
        replace_calls.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(augmentation.os, "replace", record_replace)
    write_candidates_jsonl_atomic(
        output_path,
        [
            {
                "id": "aug-external-1-001",
                "seed_id": "external-1",
                "question": "Name active employees.",
                "prompt": "Return active employee names.",
                "schema_sql": SCHEMA,
                "expected_results": [["Ada"], ["Grace"]],
                "reference_sql": "SELECT name FROM employees WHERE active = 1 ORDER BY name",
            }
        ],
    )

    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "expected_results": [["Ada"], ["Grace"]],
        "id": "aug-external-1-001",
        "prompt": "Return active employee names.",
        "question": "Name active employees.",
        "reference_sql": "SELECT name FROM employees WHERE active = 1 ORDER BY name",
        "schema_sql": SCHEMA,
        "seed_id": "external-1",
    }
    assert len(replace_calls) == 1
    assert str(replace_calls[0][0]).endswith(".tmp")
    assert replace_calls[0][1] == output_path
    assert not list(output_path.parent.glob("*.tmp"))


def test_atomic_write_keeps_existing_output_when_publish_fails(tmp_path, monkeypatch):
    output_path = tmp_path / "candidates.jsonl"
    output_path.write_text("previous-complete-output\n", encoding="utf-8")

    def fail_replace(source, destination):
        del source, destination
        raise OSError("simulated publish failure")

    monkeypatch.setattr(augmentation.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated publish failure"):
        write_candidates_jsonl_atomic(
            output_path,
            [
                {
                    "id": "aug-seed-a-001",
                    "seed_id": "seed-a",
                    "question": "Name active employees.",
                    "prompt": "Return active employee names.",
                    "schema_sql": SCHEMA,
                    "expected_results": [["Ada"], ["Grace"]],
                    "reference_sql": "SELECT name FROM employees WHERE active = 1 ORDER BY name",
                }
            ],
        )

    assert output_path.read_text(encoding="utf-8") == "previous-complete-output\n"
    assert not list(tmp_path.glob("*.tmp"))


def test_summary_write_is_atomic_and_contains_only_operational_evidence(
    tmp_path, monkeypatch
):
    output_path = tmp_path / "augmentation-summary.json"
    output_path.write_text('{"old":"summary"}\n', encoding="utf-8")
    summary = AugmentationSummary(
        seed_count=3,
        variants_per_seed=2,
        proposed_count=4,
        accepted_count=1,
        rejected_shape_count=1,
        rejected_expected_results_count=0,
        rejected_verifier_count=2,
        duplicate_count=0,
        discarded_excess_count=0,
        malformed_response_count=1,
        processed_seed_ids=("seed-a",),
        unprocessed_seed_ids=("seed-b", "seed-c"),
        retry_count=1,
    )
    replace_calls = []
    real_replace = os.replace

    def record_replace(source, destination):
        replace_calls.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(augmentation.os, "replace", record_replace)
    write_summary_json_atomic(output_path, summary)

    assert json.loads(output_path.read_text(encoding="utf-8")) == summary.as_dict()
    assert len(replace_calls) == 1
    assert str(replace_calls[0][0]).endswith(".tmp")
    assert replace_calls[0][1] == output_path
    assert not list(tmp_path.glob("*.tmp"))


def test_cli_parser_exposes_summary_and_timebox_options():
    from scripts.augment_nl2sql import _parser

    args = _parser().parse_args(
        [
            "--output",
            "candidates.jsonl",
            "--summary",
            "summary.json",
            "--timebox-minutes",
            "30",
        ]
    )

    assert args.output == Path("candidates.jsonl")
    assert args.summary == Path("summary.json")
    assert args.timebox_minutes == 30.0
