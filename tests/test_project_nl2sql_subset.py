import json
from pathlib import Path

import pytest

from scripts import project_nl2sql_subset as projection
from trainer.data.nl2sql_v1 import load_cases, split_cases


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "trainer" / "data" / "nl2sql_v1.jsonl"


def _seeds() -> list[dict[str, object]]:
    return projection.load_original_seed_rows(FIXTURE_PATH)


def _candidate(seed: dict[str, object], candidate_id: str) -> dict[str, object]:
    return {
        "id": candidate_id,
        "seed_id": seed["id"],
        "question": f"Variant of {seed['question']} ({candidate_id})",
        "prompt": seed["prompt"],
        "schema_sql": seed["schema_sql"],
        "expected_results": seed["expected_results"],
        "reference_sql": seed["reference_sql"],
    }


def _state(
    seeds: list[dict[str, object]], *, unprocessed: set[str] | None = None
) -> projection.S1RunState:
    unprocessed = unprocessed or set()
    seed_ids = {str(seed["id"]) for seed in seeds}
    return projection.S1RunState(
        processed_seed_ids=frozenset(seed_ids - unprocessed),
        unprocessed_seed_ids=frozenset(unprocessed),
    )


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_summary(path: Path, state: projection.S1RunState) -> None:
    path.write_text(
        json.dumps(
            {
                "processed_seed_ids": sorted(state.processed_seed_ids),
                "unprocessed_seed_ids": sorted(state.unprocessed_seed_ids),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def test_projection_is_deterministic_and_selects_smallest_candidate_id() -> None:
    seeds = _seeds()
    candidates = [_candidate(seed, f"aug-{seed['id']}-b") for seed in seeds]
    candidates.append(_candidate(seeds[0], "aug-v1-001-a"))

    first = projection.project_subset(
        candidates=candidates,
        original_seeds=seeds,
        run_state=_state(seeds),
    )
    second = projection.project_subset(
        candidates=list(reversed(candidates)),
        original_seeds=seeds,
        run_state=_state(seeds),
    )

    assert json.dumps(first.rows, sort_keys=True) == json.dumps(second.rows, sort_keys=True)
    assert [row["id"] for row in first.rows] == list(projection.CANONICAL_SEED_IDS)
    assert first.rows[0]["source_candidate_id"] == "aug-v1-001-a"
    assert first.rows[0]["selection_reason"] == "candidate_lexicographically_smallest_id"
    assert first.fallback_count == 0


def test_projection_discards_candidate_that_fails_independent_reverification() -> None:
    seeds = _seeds()
    candidates = [_candidate(seed, f"aug-{seed['id']}-b") for seed in seeds]
    invalid = _candidate(seeds[0], "aug-v1-001-a")
    invalid["reference_sql"] = "SELECT 'wrong result'"
    candidates.append(invalid)

    result = projection.project_subset(
        candidates=candidates,
        original_seeds=seeds,
        run_state=_state(seeds),
    )

    assert result.rows[0]["source_candidate_id"] == "aug-v1-001-b"
    assert result.discarded_candidate_count == 1
    assert result.fallback_count == 0


def test_projection_classifies_unprocessed_and_processed_fallbacks() -> None:
    seeds = _seeds()
    unprocessed_seed = str(seeds[0]["id"])
    processed_empty_seed = str(seeds[1]["id"])
    candidates = [
        _candidate(seed, f"aug-{seed['id']}-a")
        for seed in seeds
        if seed["id"] not in {unprocessed_seed, processed_empty_seed}
    ]

    result = projection.project_subset(
        candidates=candidates,
        original_seeds=seeds,
        run_state=_state(seeds, unprocessed={unprocessed_seed}),
    )

    assert result.rows[0]["source_candidate_id"] is None
    assert result.rows[0]["selection_reason"] == "fallback_unprocessed_seed"
    assert result.rows[1]["source_candidate_id"] is None
    assert result.rows[1]["selection_reason"] == "fallback_processed_no_eligible_candidate"
    assert result.fallback_count == 2
    assert result.fallback_unprocessed_count == 1
    assert result.fallback_processed_count == 1


def test_too_many_fallbacks_do_not_overwrite_existing_output(tmp_path: Path) -> None:
    seeds = _seeds()
    selected = seeds[:39]
    unprocessed = {str(seed["id"]) for seed in seeds[39:]}
    state = _state(seeds, unprocessed=unprocessed)
    candidates_path = tmp_path / "full.jsonl"
    summary_path = tmp_path / "summary.json"
    output_path = tmp_path / "subset.jsonl"
    _write_jsonl(
        candidates_path,
        [_candidate(seed, f"aug-{seed['id']}-a") for seed in selected],
    )
    _write_summary(summary_path, state)
    output_path.write_text("previous-complete-output\n", encoding="utf-8")

    with pytest.raises(projection.FallbackLimitError, match="output was not written"):
        projection.project_and_write(
            candidates_path=candidates_path,
            summary_path=summary_path,
            seeds_path=FIXTURE_PATH,
            output_path=output_path,
        )

    assert output_path.read_text(encoding="utf-8") == "previous-complete-output\n"


def test_projection_output_remains_compatible_with_fixed_fifty_row_loader(
    tmp_path: Path,
) -> None:
    seeds = _seeds()
    candidates_path = tmp_path / "full.jsonl"
    summary_path = tmp_path / "summary.json"
    output_path = tmp_path / "nl2sql_v1.jsonl"
    state = _state(seeds)
    _write_jsonl(
        candidates_path,
        [_candidate(seed, f"aug-{seed['id']}-a") for seed in seeds],
    )
    _write_summary(summary_path, state)

    result = projection.project_and_write(
        candidates_path=candidates_path,
        summary_path=summary_path,
        seeds_path=FIXTURE_PATH,
        output_path=output_path,
    )
    loaded = load_cases(output_path)
    train, validation = split_cases(loaded)

    assert result.fallback_count == 0
    assert len(loaded) == 50
    assert len(train) == 40
    assert len(validation) == 10
    first_record = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])
    assert first_record["seed_id"] == "v1-001"
    assert first_record["source_candidate_id"] == "aug-v1-001-a"
