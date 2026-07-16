from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import reproject_nl2sql_difficulty as reprojection


SCHEMA = """
CREATE TABLE values_table (value INTEGER NOT NULL);
INSERT INTO values_table VALUES (1);
"""


def _population_row(
    seed_id: str,
    *,
    population_id: str | None = None,
    source_kind: str = "original_seed",
) -> dict[str, object]:
    identifier = population_id or f"original:{seed_id}"
    return {
        "id": identifier,
        "source_record_id": identifier.removeprefix("original:"),
        "seed_id": seed_id,
        "source_kind": source_kind,
        "question": f"Question for {seed_id}?",
        "prompt": f"Return one for {seed_id}.",
        "schema_sql": SCHEMA,
        "expected_results": [[1]],
        "reference_sql": "SELECT value FROM values_table",
    }


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _write_counts(path: Path, rows: list[dict[str, object]], counts: dict[str, int]) -> None:
    _write_jsonl(
        path,
        [
            {"record_index": index, "record_id": row["id"], "pass_count": counts[row["id"]], "k": 8}
            for index, row in enumerate(rows, start=1)
        ],
    )


def test_reprojection_prefers_an_exact_two_over_other_preferred_counts(
    tmp_path: Path,
) -> None:
    rows = [_population_row(seed_id) for seed_id in reprojection.CANONICAL_SEED_IDS]
    extra = _population_row(
        "v1-001", population_id="candidate:aug-a", source_kind="candidate"
    )
    rows.append(extra)
    population = tmp_path / "population.jsonl"
    counts = tmp_path / "counts.jsonl"
    _write_jsonl(population, rows)
    pass_counts = {str(row["id"]): 4 for row in rows}
    pass_counts["candidate:aug-a"] = 2
    _write_counts(counts, rows, pass_counts)

    result = reprojection.reproject_population(population, counts)

    assert result.stopped is False
    assert len(result.rows) == 50
    first = result.rows[0]
    assert first["id"] == "v1-001"
    assert first["source_population_id"] == "candidate:aug-a"
    assert first["difficulty_pass_count"] == 2
    assert first["selection_reason"] == "nearest_two_preferred"
    assert result.report["discarded_seed_ids"] == []


def test_reprojection_breaks_preferred_distance_ties_by_population_id(
    tmp_path: Path,
) -> None:
    rows = [_population_row(seed_id) for seed_id in reprojection.CANONICAL_SEED_IDS]
    extra = _population_row(
        "v1-001", population_id="candidate:aug-a", source_kind="candidate"
    )
    rows.append(extra)
    population = tmp_path / "population.jsonl"
    counts = tmp_path / "counts.jsonl"
    _write_jsonl(population, rows)
    pass_counts = {str(row["id"]): 2 for row in rows}
    pass_counts["original:v1-001"] = 3
    pass_counts["candidate:aug-a"] = 1
    _write_counts(counts, rows, pass_counts)

    result = reprojection.reproject_population(population, counts)

    assert result.stopped is False
    assert result.rows[0]["source_population_id"] == "candidate:aug-a"
    assert result.rows[0]["difficulty_pass_count"] == 1


def test_reprojection_backfills_discarded_seed_from_second_mixed_source_row(
    tmp_path: Path,
) -> None:
    rows = [_population_row(seed_id) for seed_id in reprojection.CANONICAL_SEED_IDS]
    extra = _population_row(
        "v1-002", population_id="candidate:aug-v1-002", source_kind="candidate"
    )
    rows.append(extra)
    population = tmp_path / "population.jsonl"
    counts = tmp_path / "counts.jsonl"
    _write_jsonl(population, rows)
    pass_counts = {str(row["id"]): 4 for row in rows}
    pass_counts["original:v1-001"] = 0
    pass_counts["candidate:aug-v1-002"] = 3
    _write_counts(counts, rows, pass_counts)

    result = reprojection.reproject_population(population, counts)

    assert result.stopped is False
    first = result.rows[0]
    assert first["id"] == "v1-001"
    assert first["source_seed_id"] == "v1-002"
    assert first["source_population_id"] == "original:v1-002"
    assert first["selection_reason"] == "backfill_next_best_eligible"
    assert result.report["backfills"] == [
        {
            "slot_seed_id": "v1-001",
            "source_seed_id": "v1-002",
            "source_population_id": "original:v1-002",
            "pass_count": 4,
        }
    ]
    assert result.report["source_seed_use"]["v1-002"] == 2


def test_reprojection_uses_lowest_relaxed_count_when_no_preferred_row_exists(
    tmp_path: Path,
) -> None:
    rows = [_population_row(seed_id) for seed_id in reprojection.CANONICAL_SEED_IDS]
    extra = _population_row(
        "v1-001", population_id="candidate:aug-v1-001", source_kind="candidate"
    )
    rows.append(extra)
    population = tmp_path / "population.jsonl"
    counts = tmp_path / "counts.jsonl"
    _write_jsonl(population, rows)
    pass_counts = {str(row["id"]): 2 for row in rows}
    pass_counts["original:v1-001"] = 6
    pass_counts["candidate:aug-v1-001"] = 5
    _write_counts(counts, rows, pass_counts)

    result = reprojection.reproject_population(population, counts)

    assert result.stopped is False
    first = result.rows[0]
    assert first["source_population_id"] == "candidate:aug-v1-001"
    assert first["difficulty_pass_count"] == 5
    assert first["selection_reason"] == "lowest_relaxed"


def test_reprojection_stops_before_output_when_more_than_twenty_seeds_are_unmixed(
    tmp_path: Path,
) -> None:
    rows = [_population_row(seed_id) for seed_id in reprojection.CANONICAL_SEED_IDS]
    population = tmp_path / "population.jsonl"
    counts = tmp_path / "counts.jsonl"
    _write_jsonl(population, rows)
    pass_counts = {str(row["id"]): 4 for row in rows}
    for index in range(1, 22):
        pass_counts[f"original:v1-{index:03d}"] = 0
    _write_counts(counts, rows, pass_counts)

    result = reprojection.reproject_population(population, counts)

    assert result.stopped is True
    assert result.rows == ()
    assert result.report["stop_reason"] == "discard_limit_exceeded"
    assert len(result.report["discarded_seed_ids"]) == 21


def test_cli_writes_stop_report_without_writing_subset(tmp_path: Path) -> None:
    rows = [_population_row(seed_id) for seed_id in reprojection.CANONICAL_SEED_IDS]
    population = tmp_path / "population.jsonl"
    counts = tmp_path / "counts.jsonl"
    output = tmp_path / "projected.jsonl"
    report = tmp_path / "report.json"
    _write_jsonl(population, rows)
    pass_counts = {str(row["id"]): 0 for row in rows}
    _write_counts(counts, rows, pass_counts)

    assert (
        reprojection.main(
            [
                "--population",
                str(population),
                "--pass-counts",
                str(counts),
                "--output",
                str(output),
                "--report",
                str(report),
            ]
        )
        == 1
    )
    assert not output.exists()
    assert json.loads(report.read_text(encoding="utf-8"))["stop_reason"] == "discard_limit_exceeded"


def test_projection_output_atomic_publish_keeps_prior_complete_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "projected.jsonl"
    output.write_text('{"previous":true}\n', encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("simulated publish failure")

    monkeypatch.setattr(reprojection.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated publish failure"):
        reprojection.write_text_atomic(output, '{"new":true}\n')

    assert output.read_text(encoding="utf-8") == '{"previous":true}\n'
    assert not list(tmp_path.glob(".projected.jsonl.*"))
