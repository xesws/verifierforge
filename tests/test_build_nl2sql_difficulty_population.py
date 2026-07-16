from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import build_nl2sql_difficulty_population as population


def _seed(index: int) -> dict[str, object]:
    return {
        "id": f"v1-{index:03d}",
        "question": f"Question {index}?",
        "reference_sql": "SELECT 1",
        "expected_results": [[1]],
    }


def _candidate(identifier: str, seed_id: str = "v1-001") -> dict[str, object]:
    return {
        "id": identifier,
        "seed_id": seed_id,
        "question": "Candidate question?",
        "prompt": "Return SQL.",
        "schema_sql": "CREATE TABLE t (id INTEGER);",
        "expected_results": [],
        "reference_sql": "SELECT id FROM t",
    }


def test_build_population_binds_candidates_and_all_original_seed_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(json.dumps(_candidate("aug-1")) + "\n", encoding="utf-8")
    seed_rows = [_seed(index) for index in range(1, 51)]
    seed_bytes = ("".join(json.dumps(row) + "\n" for row in seed_rows)).encode()
    monkeypatch.setattr(
        population,
        "read_original_seed_rows",
        lambda seed_ref: (seed_rows, seed_bytes),
    )

    rows, manifest = population.build_population(candidates, "fixture-ref")

    assert len(rows) == 51
    assert [row["id"] for row in rows] == sorted(row["id"] for row in rows)
    assert rows[0]["id"] == "candidate:aug-1"
    assert rows[-1]["id"] == "original:v1-050"
    assert rows[-1]["prompt"].endswith("Question: Question 50?\nSQL:")
    assert manifest["candidate_source"]["sha256"] == hashlib.sha256(
        candidates.read_bytes()
    ).hexdigest()
    assert manifest["original_seed_source"] == {
        "git_ref": "fixture-ref",
        "git_path": "trainer/data/nl2sql_v1.jsonl",
        "sha256": hashlib.sha256(seed_bytes).hexdigest(),
        "record_count": 50,
    }


def test_build_population_rejects_duplicate_candidate_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(
        "".join(json.dumps(_candidate("duplicate")) + "\n" for _ in range(2)),
        encoding="utf-8",
    )
    seed_rows = [_seed(index) for index in range(1, 51)]
    monkeypatch.setattr(
        population,
        "read_original_seed_rows",
        lambda seed_ref: (seed_rows, b"seed-bytes"),
    )

    with pytest.raises(population.PopulationError, match="not unique"):
        population.build_population(candidates, "fixture-ref")


def test_population_jsonl_atomic_publish_keeps_prior_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "population.jsonl"
    output.write_text('{"previous":true}\n', encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("simulated publish failure")

    monkeypatch.setattr(population.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated publish failure"):
        population.write_jsonl_atomic(output, [{"id": "new"}])

    assert output.read_text(encoding="utf-8") == '{"previous":true}\n'
    assert not list(tmp_path.glob(".population.jsonl.*"))
