#!/usr/bin/env python3
"""Deterministically project S1 NL2SQL candidates into the fixed V1 fixture.

This tool never calls an LLM.  It reads the bounded S1 candidate JSONL and its
count-only run summary, independently rechecks candidate SQL, then publishes
one canonical row for each reviewed V1 seed.  The output intentionally keeps
the existing trainer fields while adding the provenance Gate A needs.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from core.rewards.nl2sql import NL2SQLVerifier  # noqa: E402 - path setup above.
from trainer.data.nl2sql_v1 import load_cases  # noqa: E402 - path setup above.


DEFAULT_SEEDS = REPOSITORY_ROOT / "trainer" / "data" / "nl2sql_v1.jsonl"
CANONICAL_SEED_IDS = tuple(f"v1-{number:03d}" for number in range(1, 51))


class ProjectionError(ValueError):
    """Raised when S1 inputs cannot safely produce a reproducible subset."""


class FallbackLimitError(ProjectionError):
    """Raised before publication when too many original rows are needed."""


@dataclass(frozen=True)
class S1RunState:
    """The seed-completion accounting needed to explain projection fallbacks."""

    processed_seed_ids: frozenset[str]
    unprocessed_seed_ids: frozenset[str]


@dataclass(frozen=True)
class ProjectionResult:
    """Rows and count-only provenance from one deterministic projection."""

    rows: tuple[dict[str, Any], ...]
    candidate_count: int
    eligible_candidate_count: int
    discarded_candidate_count: int
    fallback_count: int
    fallback_processed_count: int
    fallback_unprocessed_count: int

    def as_dict(self) -> dict[str, int]:
        """Return safe, stable counters for the command's JSON result."""
        return {
            "candidate_count": self.candidate_count,
            "discarded_candidate_count": self.discarded_candidate_count,
            "eligible_candidate_count": self.eligible_candidate_count,
            "fallback_count": self.fallback_count,
            "fallback_processed_count": self.fallback_processed_count,
            "fallback_unprocessed_count": self.fallback_unprocessed_count,
            "row_count": len(self.rows),
        }


def build_parser() -> argparse.ArgumentParser:
    """Build the intentionally explicit command-line interface."""
    parser = argparse.ArgumentParser(
        description="Project verifier-screened NL2SQL candidates into 50 V1 rows."
    )
    parser.add_argument(
        "--candidates",
        required=True,
        type=Path,
        help="S1 full candidate JSONL.",
    )
    parser.add_argument(
        "--summary",
        required=True,
        type=Path,
        help="S1 count-only summary JSON containing processed/unprocessed seed IDs.",
    )
    parser.add_argument(
        "--seeds",
        type=Path,
        default=DEFAULT_SEEDS,
        help="Original reviewed 50-row V1 fixture (default: trainer/data/nl2sql_v1.jsonl).",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Destination subset JSONL; publication is atomic.",
    )
    parser.add_argument(
        "--max-fallbacks",
        type=int,
        default=10,
        help="Maximum original-seed fallbacks allowed before publication (default: 10).",
    )
    return parser


def load_s1_run_state(path: Path, expected_seed_ids: Sequence[str]) -> S1RunState:
    """Load and validate the exact S1 seed partition used for fallback labels."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ProjectionError(f"S1 summary does not exist: {path}") from error
    except OSError as error:
        raise ProjectionError(f"cannot read S1 summary: {path}") from error
    except json.JSONDecodeError as error:
        raise ProjectionError(f"invalid JSON in S1 summary: {path}") from error

    if not isinstance(payload, Mapping):
        raise ProjectionError("S1 summary must be a JSON object")
    processed = _seed_id_set(payload.get("processed_seed_ids"), "processed_seed_ids")
    unprocessed = _seed_id_set(
        payload.get("unprocessed_seed_ids"), "unprocessed_seed_ids"
    )

    overlap = processed & unprocessed
    if overlap:
        raise ProjectionError(
            "S1 summary processed/unprocessed seed IDs overlap: "
            + ", ".join(sorted(overlap))
        )
    expected = set(expected_seed_ids)
    if processed | unprocessed != expected:
        missing = expected - (processed | unprocessed)
        extra = (processed | unprocessed) - expected
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unknown " + ", ".join(sorted(extra)))
        raise ProjectionError("S1 summary must partition all V1 seeds: " + "; ".join(details))
    return S1RunState(
        processed_seed_ids=frozenset(processed),
        unprocessed_seed_ids=frozenset(unprocessed),
    )


def load_candidate_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load candidate objects without trusting their contents as admission proof."""
    if not path.is_file():
        raise ProjectionError(f"candidate JSONL does not exist: {path}")

    rows: list[dict[str, Any]] = []
    try:
        source = path.open(encoding="utf-8")
    except OSError as error:
        raise ProjectionError(f"cannot read candidate JSONL: {path}") from error
    with source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ProjectionError(
                    f"invalid JSON in candidate JSONL line {line_number}"
                ) from error
            if not isinstance(row, Mapping):
                raise ProjectionError(
                    f"candidate JSONL line {line_number} must be an object"
                )
            rows.append(dict(row))
    return rows


def load_original_seed_rows(path: Path) -> list[dict[str, Any]]:
    """Load original V1 rows and attach the trusted prompt/schema used by reward."""
    raw_rows = load_candidate_jsonl(path)
    try:
        enriched_cases = load_cases(path)
    except (OSError, ValueError) as error:
        raise ProjectionError(f"invalid original V1 fixture: {error}") from error

    if len(raw_rows) != len(enriched_cases):
        raise ProjectionError("original V1 fixture contains blank or malformed rows")
    raw_by_id: dict[str, Mapping[str, Any]] = {}
    for line_number, raw in enumerate(raw_rows, start=1):
        seed_id = raw.get("id")
        if not _nonempty_text(seed_id):
            raise ProjectionError(f"original V1 fixture line {line_number} has invalid id")
        if seed_id in raw_by_id:
            raise ProjectionError(f"original V1 fixture has duplicate id: {seed_id}")
        raw_by_id[seed_id] = raw

    seed_ids = [case["id"] for case in enriched_cases]
    if tuple(seed_ids) != CANONICAL_SEED_IDS:
        raise ProjectionError("original V1 fixture must contain v1-001 through v1-050")

    seed_rows: list[dict[str, Any]] = []
    for case in enriched_cases:
        raw = raw_by_id[case["id"]]
        question = raw.get("question")
        if not _nonempty_text(question):
            raise ProjectionError(f"original V1 fixture {case['id']} has invalid question")
        seed_rows.append(
            {
                "id": case["id"],
                "question": question,
                "prompt": case["prompt"],
                "schema_sql": case["schema_sql"],
                "expected_results": case["expected_results"],
                "reference_sql": case["reference_sql"],
            }
        )
    return seed_rows


def project_subset(
    *,
    candidates: Sequence[Mapping[str, Any]],
    original_seeds: Sequence[Mapping[str, Any]],
    run_state: S1RunState,
    max_fallbacks: int = 10,
) -> ProjectionResult:
    """Build exactly one exact-verifier-admitted row for every canonical seed.

    Candidate rows are deliberately rechecked rather than trusted because the
    S1 artifact is an intermediate result.  Fallbacks are permitted only when
    S1's explicit processed/unprocessed partition explains the empty group.
    """
    if max_fallbacks < 0:
        raise ProjectionError("max_fallbacks must be non-negative")

    seed_by_id = _index_original_seeds(original_seeds)
    expected_seed_ids = tuple(seed_by_id)
    _validate_run_state(run_state, expected_seed_ids)
    grouped_candidates = _group_candidates(candidates, set(expected_seed_ids))

    rows: list[dict[str, Any]] = []
    eligible_candidate_count = 0
    discarded_candidate_count = 0
    fallback_processed_count = 0
    fallback_unprocessed_count = 0

    for seed_id in expected_seed_ids:
        seed = seed_by_id[seed_id]
        raw_group = grouped_candidates[seed_id]
        if seed_id in run_state.unprocessed_seed_ids and raw_group:
            raise ProjectionError(
                f"S1 marks {seed_id} unprocessed but candidates were supplied"
            )

        eligible, discarded = _eligible_candidates(seed, raw_group)
        eligible_candidate_count += len(eligible)
        discarded_candidate_count += discarded
        if eligible:
            chosen = min(eligible, key=lambda row: row["id"])
            rows.append(_selected_candidate_row(seed, chosen))
            continue

        if seed_id in run_state.unprocessed_seed_ids:
            selection_reason = "fallback_unprocessed_seed"
            fallback_unprocessed_count += 1
        else:
            selection_reason = "fallback_processed_no_eligible_candidate"
            fallback_processed_count += 1
        rows.append(_fallback_seed_row(seed, selection_reason))

    fallback_count = fallback_processed_count + fallback_unprocessed_count
    if fallback_count > max_fallbacks:
        raise FallbackLimitError(
            f"fallback count {fallback_count} exceeds limit {max_fallbacks}; output was not written"
        )
    return ProjectionResult(
        rows=tuple(rows),
        candidate_count=len(candidates),
        eligible_candidate_count=eligible_candidate_count,
        discarded_candidate_count=discarded_candidate_count,
        fallback_count=fallback_count,
        fallback_processed_count=fallback_processed_count,
        fallback_unprocessed_count=fallback_unprocessed_count,
    )


def project_and_write(
    *,
    candidates_path: Path,
    summary_path: Path,
    seeds_path: Path,
    output_path: Path,
    max_fallbacks: int = 10,
) -> ProjectionResult:
    """Load all inputs, enforce the fallback gate, then atomically publish rows."""
    original_seeds = load_original_seed_rows(seeds_path)
    run_state = load_s1_run_state(
        summary_path, [str(seed["id"]) for seed in original_seeds]
    )
    result = project_subset(
        candidates=load_candidate_jsonl(candidates_path),
        original_seeds=original_seeds,
        run_state=run_state,
        max_fallbacks=max_fallbacks,
    )
    write_subset_jsonl_atomic(output_path, result.rows)
    return result


def write_subset_jsonl_atomic(
    output_path: Path, rows: Sequence[Mapping[str, Any]]
) -> Path:
    """Replace the output only after a complete deterministic JSONL payload exists."""
    payload = "".join(_canonical_json(dict(row)) + "\n" for row in rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(payload)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, output_path)
        return output_path
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _index_original_seeds(
    original_seeds: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for seed in original_seeds:
        seed_id = seed.get("id")
        if not _nonempty_text(seed_id):
            raise ProjectionError("original seed has an invalid id")
        if seed_id in indexed:
            raise ProjectionError(f"original seed IDs are duplicated: {seed_id}")
        if not _valid_seed_fields(seed):
            raise ProjectionError(f"original seed {seed_id} has invalid required fields")
        indexed[seed_id] = seed
    if tuple(indexed) != CANONICAL_SEED_IDS:
        raise ProjectionError("original seeds must be sorted v1-001 through v1-050")
    return indexed


def _validate_run_state(run_state: S1RunState, expected_seed_ids: Sequence[str]) -> None:
    expected = set(expected_seed_ids)
    processed = set(run_state.processed_seed_ids)
    unprocessed = set(run_state.unprocessed_seed_ids)
    if processed & unprocessed or processed | unprocessed != expected:
        raise ProjectionError("S1 run state must partition all original seed IDs")


def _group_candidates(
    candidates: Sequence[Mapping[str, Any]], expected_seed_ids: set[str]
) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for index, candidate in enumerate(candidates, start=1):
        seed_id = candidate.get("seed_id")
        if not _nonempty_text(seed_id):
            raise ProjectionError(f"candidate {index} has an invalid seed_id")
        if seed_id not in expected_seed_ids:
            raise ProjectionError(f"candidate {index} has unknown seed_id: {seed_id}")
        grouped[seed_id].append(candidate)
    return grouped


def _eligible_candidates(
    seed: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    """Discard malformed, duplicate-ID, or non-exact candidates independently."""
    id_counts = Counter(
        candidate["id"]
        for candidate in candidates
        if _nonempty_text(candidate.get("id"))
    )
    eligible: list[dict[str, Any]] = []
    discarded = 0
    for candidate in candidates:
        candidate_id = candidate.get("id")
        if not _valid_record_fields(candidate):
            discarded += 1
            continue
        if id_counts[candidate_id] != 1:
            discarded += 1
            continue
        if not _same_seed_execution_context(seed, candidate):
            discarded += 1
            continue
        verifier = NL2SQLVerifier(candidate["schema_sql"], candidate["expected_results"])
        if verifier.score(candidate["prompt"], candidate["reference_sql"]) != 1.0:
            discarded += 1
            continue
        eligible.append(dict(candidate))
    return eligible, discarded


def _same_seed_execution_context(
    seed: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    return (
        candidate["schema_sql"] == seed["schema_sql"]
        and _canonical_json(candidate["expected_results"])
        == _canonical_json(seed["expected_results"])
    )


def _selected_candidate_row(
    seed: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "id": seed["id"],
        "seed_id": seed["id"],
        "source_candidate_id": candidate["id"],
        "selection_reason": "candidate_lexicographically_smallest_id",
        "question": candidate["question"],
        "prompt": candidate["prompt"],
        "schema_sql": candidate["schema_sql"],
        "expected_results": candidate["expected_results"],
        "reference_sql": candidate["reference_sql"],
    }


def _fallback_seed_row(seed: Mapping[str, Any], selection_reason: str) -> dict[str, Any]:
    verifier = NL2SQLVerifier(seed["schema_sql"], seed["expected_results"])
    if verifier.score(seed["prompt"], seed["reference_sql"]) != 1.0:
        raise ProjectionError(f"original fallback seed failed re-verification: {seed['id']}")
    return {
        "id": seed["id"],
        "seed_id": seed["id"],
        "source_candidate_id": None,
        "selection_reason": selection_reason,
        "question": seed["question"],
        "prompt": seed["prompt"],
        "schema_sql": seed["schema_sql"],
        "expected_results": seed["expected_results"],
        "reference_sql": seed["reference_sql"],
    }


def _valid_record_fields(record: Mapping[str, Any]) -> bool:
    return all(
        _nonempty_text(record.get(field))
        for field in ("id", "seed_id", "question", "prompt", "schema_sql", "reference_sql")
    ) and _result_rows(record.get("expected_results"))


def _valid_seed_fields(seed: Mapping[str, Any]) -> bool:
    return all(
        _nonempty_text(seed.get(field))
        for field in ("id", "question", "prompt", "schema_sql", "reference_sql")
    ) and _result_rows(seed.get("expected_results"))


def _result_rows(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(row, list) for row in value)


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _seed_id_set(value: Any, field: str) -> set[str]:
    if not isinstance(value, list) or not all(_nonempty_text(seed_id) for seed_id in value):
        raise ProjectionError(f"S1 summary {field} must be a list of non-empty strings")
    seed_ids = set(value)
    if len(seed_ids) != len(value):
        raise ProjectionError(f"S1 summary {field} contains duplicate seed IDs")
    return seed_ids


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise ProjectionError("record contains a non-JSON value") from error


def main(argv: Sequence[str] | None = None) -> int:
    """Publish a validated subset and return shell-friendly failure codes."""
    args = build_parser().parse_args(argv)
    try:
        result = project_and_write(
            candidates_path=args.candidates,
            summary_path=args.summary,
            seeds_path=args.seeds,
            output_path=args.output,
            max_fallbacks=args.max_fallbacks,
        )
    except (FallbackLimitError, OSError, ProjectionError) as error:
        print(f"projection error: {error}", file=sys.stderr)
        return 2
    output = result.as_dict()
    output["output_path"] = str(args.output)
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
