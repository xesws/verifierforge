#!/usr/bin/env python3
"""Build U1's 50-row training pool and a disjoint stratified held-out set."""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from core.rewards.nl2sql import NL2SQLVerifier  # noqa: E402
from scripts import reproject_nl2sql_difficulty as projection  # noqa: E402


HELD_OUT_SIZE = 60


class SplitError(ValueError):
    """Raised when a verifier-bound training/held-out split is unsafe."""


@dataclass(frozen=True)
class SplitResult:
    """The two datasets and provenance payload for one deterministic split."""

    training_rows: tuple[dict[str, Any], ...]
    heldout_rows: tuple[dict[str, Any], ...]
    report: dict[str, Any]
    stopped: bool


def build_parser() -> argparse.ArgumentParser:
    """Build the explicit U3 split command-line interface."""
    parser = argparse.ArgumentParser(
        description="Create disjoint U1 training and held-out NL2SQL datasets."
    )
    parser.add_argument("--population", required=True, type=Path)
    parser.add_argument("--pass-counts", required=True, type=Path)
    parser.add_argument("--training-output", required=True, type=Path)
    parser.add_argument("--heldout-output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Publish the two datasets only when all U1/U3 invariants hold."""
    args = build_parser().parse_args(argv)
    try:
        result = build_split(args.population, args.pass_counts)
        report = dict(result.report)
        if result.stopped:
            write_json_atomic(args.report, report)
            print(json.dumps(_display(result), sort_keys=True))
            return 1
        write_jsonl_atomic(args.training_output, result.training_rows)
        write_jsonl_atomic(args.heldout_output, result.heldout_rows)
        report["training_pool"] = _artifact_descriptor(args.training_output)
        report["heldout_pool"] = _artifact_descriptor(args.heldout_output)
        write_json_atomic(args.report, report)
    except (OSError, SplitError, projection.ReprojectionError) as error:
        print(f"split_nl2sql_training_heldout error: {error}", file=sys.stderr)
        return 2

    print(json.dumps(_display(result), sort_keys=True))
    return 0


def build_split(population_path: Path, pass_counts_path: Path) -> SplitResult:
    """Construct a verifier-v2 training/held-out split without writing files."""
    training_result = projection.reproject_population(population_path, pass_counts_path)
    base_report = {
        "schema_version": 1,
        "status": "stopped" if training_result.stopped else "completed",
        "training_selection_rule_version": projection.SELECTION_RULE_VERSION,
        "training_selection": training_result.report,
        "heldout_rule_version": "v0.10.0-bucket-largest-remainder-v1",
        "heldout_target_size": HELD_OUT_SIZE,
    }
    if training_result.stopped:
        base_report["stop_reason"] = training_result.report["stop_reason"]
        return SplitResult((), (), base_report, stopped=True)

    population_bytes = population_path.read_bytes()
    counts_bytes = pass_counts_path.read_bytes()
    population_rows = _load_jsonl(population_bytes, population_path, "population")
    count_by_id = _load_pass_counts(counts_bytes, pass_counts_path)
    population_by_id = _index_population(population_rows)
    if set(population_by_id) != set(count_by_id):
        raise SplitError("population IDs and pass-count IDs must match exactly")

    training_rows = tuple({**row, "split": "training"} for row in training_result.rows)
    training_source_ids = {str(row["source_population_id"]) for row in training_rows}
    if len(training_source_ids) != len(training_rows):
        raise SplitError("training pool reuses a source population ID")

    remaining = [
        row
        for population_id, row in population_by_id.items()
        if population_id not in training_source_ids
    ]
    if len(remaining) < HELD_OUT_SIZE:
        raise SplitError(
            "not enough population records remain for the held-out target"
        )
    heldout_population_ids, allocation = _select_heldout_ids(remaining, count_by_id)
    heldout_rows = tuple(
        _heldout_row(population_by_id[population_id], count_by_id[population_id])
        for population_id in heldout_population_ids
    )
    heldout_source_ids = {str(row["source_population_id"]) for row in heldout_rows}
    if training_source_ids & heldout_source_ids:
        raise SplitError("training and held-out source population IDs overlap")
    if len(heldout_rows) != HELD_OUT_SIZE:
        raise SplitError("held-out selection did not produce 60 records")

    verification_failures = _verify_rows((*training_rows, *heldout_rows))
    if verification_failures:
        base_report.update(
            {
                "status": "stopped",
                "stop_reason": "reference_sql_reverification_failed",
                "reference_reverification": {
                    "record_count": len(training_rows) + len(heldout_rows),
                    "full_pass_count": len(training_rows) + len(heldout_rows) - len(verification_failures),
                    "failures": verification_failures,
                },
            }
        )
        return SplitResult((), (), base_report, stopped=True)

    source = (REPOSITORY_ROOT / "core" / "rewards" / "nl2sql.py").read_bytes()
    base_report.update(
        {
            "stop_reason": None,
            "source_population": {
                "path": str(population_path),
                "sha256": hashlib.sha256(population_bytes).hexdigest(),
                "record_count": len(population_rows),
            },
            "source_pass_counts": {
                "path": str(pass_counts_path),
                "sha256": hashlib.sha256(counts_bytes).hexdigest(),
                "record_count": len(count_by_id),
                "k": 8,
            },
            "verifier": {
                "identity": "core.rewards.nl2sql.NL2SQLVerifier",
                "version": NL2SQLVerifier.VERSION,
                "source_sha256": hashlib.sha256(source).hexdigest(),
            },
            "reference_reverification": {
                "record_count": len(training_rows) + len(heldout_rows),
                "full_pass_count": len(training_rows) + len(heldout_rows),
                "failures": [],
            },
            "zero_source_overlap": True,
            "heldout_bucket_allocation": allocation,
        }
    )
    return SplitResult(training_rows, heldout_rows, base_report, stopped=False)


def write_jsonl_atomic(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    """Atomically write canonical split JSONL."""
    write_text_atomic(path, _jsonl_content(rows))


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically write a canonical split provenance report."""
    write_text_atomic(path, json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")


def _select_heldout_ids(
    remaining: Sequence[Mapping[str, Any]], count_by_id: Mapping[str, int]
) -> tuple[tuple[str, ...], dict[str, dict[str, int]]]:
    buckets: dict[int, list[str]] = defaultdict(list)
    for row in remaining:
        population_id = str(row["id"])
        count = count_by_id[population_id]
        buckets[count].append(population_id)
    for ids in buckets.values():
        ids.sort()
    nonempty = sorted(buckets)
    if len(nonempty) > HELD_OUT_SIZE:
        raise SplitError("too many nonempty difficulty buckets for held-out coverage")

    allocation = {bucket: 1 for bucket in nonempty}
    remaining_slots = HELD_OUT_SIZE - len(nonempty)
    capacities = {bucket: len(buckets[bucket]) - 1 for bucket in nonempty}
    capacity_total = sum(capacities.values())
    if capacity_total < remaining_slots:
        raise SplitError("held-out buckets lack enough remaining capacity")
    if remaining_slots:
        exact = {
            bucket: Fraction(remaining_slots * capacities[bucket], capacity_total)
            for bucket in nonempty
        }
        for bucket in nonempty:
            allocation[bucket] += exact[bucket].numerator // exact[bucket].denominator
        leftovers = HELD_OUT_SIZE - sum(allocation.values())
        for bucket in sorted(
            nonempty,
            key=lambda bucket: (
                -(exact[bucket] - (exact[bucket].numerator // exact[bucket].denominator)),
                bucket,
            ),
        )[:leftovers]:
            allocation[bucket] += 1

    selected = tuple(
        population_id
        for bucket in nonempty
        for population_id in buckets[bucket][: allocation[bucket]]
    )
    if len(selected) != HELD_OUT_SIZE:
        raise SplitError("held-out allocation did not reach its target size")
    return selected, {
        str(bucket): {
            "available": len(buckets[bucket]),
            "selected": allocation[bucket],
        }
        for bucket in nonempty
    }


def _heldout_row(row: Mapping[str, Any], pass_count: int) -> dict[str, Any]:
    return {
        "id": row["id"],
        "source_population_id": row["id"],
        "seed_id": row["seed_id"],
        "source_record_id": row["source_record_id"],
        "source_kind": row["source_kind"],
        "split": "held_out",
        "difficulty_pass_count": pass_count,
        "question": row["question"],
        "prompt": row["prompt"],
        "schema_sql": row["schema_sql"],
        "expected_results": row["expected_results"],
        "reference_sql": row["reference_sql"],
    }


def _verify_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for row in rows:
        verifier = NL2SQLVerifier(row["schema_sql"], row["expected_results"])
        breakdown = verifier.score_breakdown(row["prompt"], row["reference_sql"])
        if breakdown.final_score != 1.0:
            failures.append(
                {
                    "id": row["id"],
                    "final_score": breakdown.final_score,
                    "failure_class": breakdown.failure_class,
                    "failure_detail": breakdown.failure_detail,
                }
            )
    return failures


def _load_pass_counts(content: bytes, path: Path) -> dict[str, int]:
    rows = _load_jsonl(content, path, "pass-count")
    counts: dict[str, int] = {}
    for index, row in enumerate(rows, start=1):
        record_id = row.get("record_id")
        pass_count = row.get("pass_count")
        if not isinstance(record_id, str) or not record_id:
            raise SplitError(f"pass-count record {index} has invalid record_id")
        if record_id in counts:
            raise SplitError(f"pass-count IDs are not unique: {record_id}")
        if isinstance(pass_count, bool) or not isinstance(pass_count, int) or not 0 <= pass_count <= 8:
            raise SplitError(f"pass-count record {record_id} has invalid pass_count")
        if row.get("k") != 8:
            raise SplitError(f"pass-count record {record_id} must have k == 8")
        counts[record_id] = pass_count
    return counts


def _index_population(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(rows, start=1):
        population_id = row.get("id")
        if not isinstance(population_id, str) or not population_id:
            raise SplitError(f"population record {index} has invalid id")
        if population_id in indexed:
            raise SplitError(f"population IDs are not unique: {population_id}")
        indexed[population_id] = row
    return indexed


def _load_jsonl(content: bytes, path: Path, label: str) -> list[dict[str, Any]]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise SplitError(f"{label} must be UTF-8 JSONL: {path}") from error
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise SplitError(f"{label} line {line_number} is not valid JSON") from error
        if not isinstance(value, Mapping):
            raise SplitError(f"{label} line {line_number} must be a JSON object")
        rows.append(dict(value))
    if not rows:
        raise SplitError(f"{label} has no records")
    return rows


def _jsonl_content(rows: Sequence[Mapping[str, Any]]) -> str:
    return "".join(
        json.dumps(dict(row), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
        for row in rows
    )


def _artifact_descriptor(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "record_count": sum(1 for line in raw.splitlines() if line.strip()),
    }


def write_text_atomic(path: Path, content: str) -> None:
    """Durably publish one artifact without exposing partial JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _display(result: SplitResult) -> dict[str, Any]:
    return {
        "status": result.report["status"],
        "training_count": len(result.training_rows),
        "heldout_count": len(result.heldout_rows),
        "stop_reason": result.report.get("stop_reason"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
