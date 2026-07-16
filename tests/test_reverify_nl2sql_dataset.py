from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from core.rewards.nl2sql import NL2SQLVerifier
from scripts import reverify_nl2sql_dataset as reverify


SCHEMA = """
CREATE TABLE people (name TEXT NOT NULL);
INSERT INTO people VALUES ('Ada');
"""


def _row(reference_sql: str, *, identifier: str = "case-1") -> dict[str, object]:
    return {
        "id": identifier,
        "prompt": "Return Ada.",
        "schema_sql": SCHEMA,
        "expected_results": [["Ada"]],
        "reference_sql": reference_sql,
    }


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_reverify_dataset_records_v2_provenance_and_all_full_passes(tmp_path: Path) -> None:
    dataset = tmp_path / "candidates.jsonl"
    _write_rows(dataset, [_row("SELECT name FROM people")])

    payload = reverify.reverify_dataset(dataset)

    assert payload["record_count"] == 1
    assert payload["full_pass_count"] == 1
    assert payload["failures"] == []
    assert payload["input"]["sha256"] == hashlib.sha256(dataset.read_bytes()).hexdigest()
    assert payload["verifier"]["version"] == NL2SQLVerifier.VERSION == 2
    assert payload["verifier"]["extraction_policy"] == "markdown_sql_fence_first_statement"


def test_reverify_dataset_records_non_full_score_without_hiding_it(tmp_path: Path) -> None:
    dataset = tmp_path / "candidates.jsonl"
    _write_rows(dataset, [_row("SELECT name FROM people WHERE name = 'Nobody'")])

    payload = reverify.reverify_dataset(dataset)

    assert payload["full_pass_count"] == 0
    assert payload["failures"] == [
        {
            "record_index": 1,
            "record_id": "case-1",
            "final_score": 0.5,
            "failure_class": "executable_not_full_pass",
            "failure_detail": "result_mismatch",
        }
    ]


def test_cli_publishes_evidence_before_returning_nonzero_for_bad_candidate(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "candidates.jsonl"
    output = tmp_path / "evidence.json"
    _write_rows(dataset, [_row("SELECT name FROM people WHERE name = 'Nobody'")])

    assert reverify.main(["--input", str(dataset), "--output", str(output)]) == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["full_pass_count"] == 0


def test_atomic_evidence_write_keeps_previous_complete_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "evidence.json"
    output.write_text('{"previous":true}\n', encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("simulated publish failure")

    monkeypatch.setattr(reverify.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated publish failure"):
        reverify.write_evidence_atomic(output, {"record_count": 1})

    assert output.read_text(encoding="utf-8") == '{"previous":true}\n'
    assert not list(tmp_path.glob(".evidence.json.*"))
