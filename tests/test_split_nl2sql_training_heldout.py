from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import split_nl2sql_training_heldout as split


SCHEMA = """
CREATE TABLE values_table (value INTEGER NOT NULL);
INSERT INTO values_table VALUES (1);
"""


def _row(seed_index: int, suffix: str) -> dict[str, object]:
    seed_id = f"v1-{seed_index:03d}"
    identifier = f"{suffix}:{seed_id}"
    return {
        "id": identifier,
        "source_record_id": identifier,
        "seed_id": seed_id,
        "source_kind": "candidate" if suffix != "original" else "original_seed",
        "question": f"Question {seed_id}?",
        "prompt": f"Return value for {seed_id}.",
        "schema_sql": SCHEMA,
        "expected_results": [[1]],
        "reference_sql": "SELECT value FROM values_table",
    }


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _population_and_counts(tmp_path: Path) -> tuple[Path, Path, list[dict[str, object]]]:
    rows = [
        row
        for index in range(1, 51)
        for row in (_row(index, "original"), _row(index, "candidate-a"), _row(index, "candidate-b"))
    ]
    population = tmp_path / "population.jsonl"
    counts = tmp_path / "counts.jsonl"
    _write_jsonl(population, rows)
    pass_counts: dict[str, int] = {}
    for index in range(1, 51):
        pass_counts[f"original:v1-{index:03d}"] = 2
        pass_counts[f"candidate-a:v1-{index:03d}"] = (index - 1) % 9
        pass_counts[f"candidate-b:v1-{index:03d}"] = index % 9
    _write_jsonl(
        counts,
        [
            {"record_index": index, "record_id": row["id"], "pass_count": pass_counts[row["id"]], "k": 8}
            for index, row in enumerate(rows, start=1)
        ],
    )
    return population, counts, rows


def test_split_builds_disjoint_50_training_and_stratified_60_heldout(tmp_path: Path) -> None:
    population, counts, _ = _population_and_counts(tmp_path)

    result = split.build_split(population, counts)

    assert result.stopped is False
    assert len(result.training_rows) == 50
    assert len(result.heldout_rows) == 60
    training_ids = {row["source_population_id"] for row in result.training_rows}
    heldout_ids = {row["source_population_id"] for row in result.heldout_rows}
    assert not training_ids & heldout_ids
    assert {row["difficulty_pass_count"] for row in result.heldout_rows} == set(range(9))
    allocation = result.report["heldout_bucket_allocation"]
    assert all(values["selected"] >= 1 for values in allocation.values())
    assert result.report["reference_reverification"] == {
        "record_count": 110,
        "full_pass_count": 110,
        "failures": [],
    }


def test_split_is_deterministic(tmp_path: Path) -> None:
    population, counts, _ = _population_and_counts(tmp_path)

    first = split.build_split(population, counts)
    second = split.build_split(population, counts)

    assert first.training_rows == second.training_rows
    assert first.heldout_rows == second.heldout_rows
    assert first.report["heldout_bucket_allocation"] == second.report["heldout_bucket_allocation"]


def test_split_cli_publishes_all_three_atomic_artifacts(tmp_path: Path) -> None:
    population, counts, _ = _population_and_counts(tmp_path)
    training = tmp_path / "training.jsonl"
    heldout = tmp_path / "heldout.jsonl"
    report = tmp_path / "split.json"

    assert (
        split.main(
            [
                "--population",
                str(population),
                "--pass-counts",
                str(counts),
                "--training-output",
                str(training),
                "--heldout-output",
                str(heldout),
                "--report",
                str(report),
            ]
        )
        == 0
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["training_pool"]["record_count"] == 50
    assert payload["heldout_pool"]["record_count"] == 60
    assert payload["zero_source_overlap"] is True


def test_split_atomic_write_keeps_prior_complete_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "heldout.jsonl"
    output.write_text('{"previous":true}\n', encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("simulated publish failure")

    monkeypatch.setattr(split.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated publish failure"):
        split.write_jsonl_atomic(output, [{"id": "new"}])

    assert output.read_text(encoding="utf-8") == '{"previous":true}\n'
    assert not list(tmp_path.glob(".heldout.jsonl.*"))
