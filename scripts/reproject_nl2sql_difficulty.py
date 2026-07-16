#!/usr/bin/env python3
"""Project a deterministic U1 NL2SQL training subset from B1 pass counts."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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


CANONICAL_SEED_IDS = tuple(f"v1-{index:03d}" for index in range(1, 51))
TARGET_PASS_COUNT = 2
PREFERRED_MIN = 1
PREFERRED_MAX = 4
RELAXED_MIN = 1
RELAXED_MAX = 6
MAX_SOURCE_SEED_ROWS = 2
MAX_DISCARDED_SEEDS = 20
SELECTION_RULE_VERSION = "v0.10.0-nearest-two-preferred-relaxed-backfill-v1"


class ReprojectionError(ValueError):
    """Raised when B2 inputs cannot safely produce a deterministic subset."""


@dataclass(frozen=True)
class CountedRecord:
    """One immutable probe record paired with its B1 full-pass count."""

    row: Mapping[str, Any]
    pass_count: int

    @property
    def population_id(self) -> str:
        return str(self.row["id"])

    @property
    def seed_id(self) -> str:
        return str(self.row["seed_id"])


@dataclass(frozen=True)
class ReprojectionResult:
    """A completed B2 selection or a documented data-layer stop."""

    rows: tuple[dict[str, Any], ...]
    report: dict[str, Any]
    stopped: bool


def build_parser() -> argparse.ArgumentParser:
    """Build the deterministic B2 command-line interface."""
    parser = argparse.ArgumentParser(
        description="Project B1 NL2SQL pass counts into U1's fixed 50-row training pool."
    )
    parser.add_argument("--population", required=True, type=Path)
    parser.add_argument("--pass-counts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Write a report and, only for a valid projection, the subset JSONL."""
    args = build_parser().parse_args(argv)
    try:
        result = reproject_population(args.population, args.pass_counts)
        report = dict(result.report)
        if result.stopped:
            write_json_atomic(args.report, report)
            print(json.dumps(_display_result(result), sort_keys=True))
            return 1
        output_content = _jsonl_content(result.rows)
        write_text_atomic(args.output, output_content)
        report["projected_subset"] = _artifact_descriptor(args.output)
        write_json_atomic(args.report, report)
    except (OSError, ReprojectionError) as error:
        print(f"reproject_nl2sql_difficulty error: {error}", file=sys.stderr)
        return 2

    print(json.dumps(_display_result(result), sort_keys=True))
    return 0


def reproject_population(population_path: Path, pass_counts_path: Path) -> ReprojectionResult:
    """Return an exact B2 selection or a stop result without writing files."""
    population_bytes = population_path.read_bytes()
    count_bytes = pass_counts_path.read_bytes()
    population = _load_jsonl(population_bytes, population_path, label="population")
    counts = _load_jsonl(count_bytes, pass_counts_path, label="pass-count")
    counted = _pair_population_with_counts(population, counts)
    verification_failures = _reverify_reference_sql(counted)
    base_report = _base_report(
        population_path=population_path,
        population_bytes=population_bytes,
        pass_counts_path=pass_counts_path,
        count_bytes=count_bytes,
        record_count=len(counted),
        verification_failures=verification_failures,
    )
    if verification_failures:
        base_report.update(
            {
                "status": "stopped",
                "stop_reason": "reference_sql_reverification_failed",
                "discarded_seed_ids": [],
                "backfills": [],
                "selected_count": 0,
            }
        )
        return ReprojectionResult(rows=(), report=base_report, stopped=True)

    grouped: dict[str, list[CountedRecord]] = defaultdict(list)
    for record in counted:
        grouped[record.seed_id].append(record)
    if tuple(sorted(grouped)) != CANONICAL_SEED_IDS:
        raise ReprojectionError("population must contain all 50 canonical seed IDs")

    selections: dict[str, CountedRecord] = {}
    selection_reasons: dict[str, str] = {}
    source_usage: Counter[str] = Counter()
    discarded: list[str] = []
    for seed_id in CANONICAL_SEED_IDS:
        selected, selection_reason = _select_for_seed(grouped[seed_id])
        if selected is None:
            discarded.append(seed_id)
            continue
        selections[seed_id] = selected
        selection_reasons[seed_id] = selection_reason
        source_usage[selected.seed_id] += 1

    if len(discarded) > MAX_DISCARDED_SEEDS:
        base_report.update(
            {
                "status": "stopped",
                "stop_reason": "discard_limit_exceeded",
                "discarded_seed_ids": discarded,
                "backfills": [],
                "selected_count": len(selections),
                "source_seed_use": dict(sorted(source_usage.items())),
            }
        )
        return ReprojectionResult(rows=(), report=base_report, stopped=True)

    selected_population_ids = {record.population_id for record in selections.values()}
    backfill_pool = sorted(
        (
            record
            for records in grouped.values()
            for record in _sorted_eligible(records)
            if record.population_id not in selected_population_ids
        ),
        key=_selection_key,
    )
    backfills: list[dict[str, Any]] = []
    for discarded_seed_id in discarded:
        replacement = next(
            (
                record
                for record in backfill_pool
                if record.population_id not in selected_population_ids
                and record.seed_id != discarded_seed_id
                and source_usage[record.seed_id] < MAX_SOURCE_SEED_ROWS
            ),
            None,
        )
        if replacement is None:
            base_report.update(
                {
                    "status": "stopped",
                    "stop_reason": "no_compliant_backfill",
                    "discarded_seed_ids": discarded,
                    "backfills": backfills,
                    "selected_count": len(selections),
                    "source_seed_use": dict(sorted(source_usage.items())),
                }
            )
            return ReprojectionResult(rows=(), report=base_report, stopped=True)
        selections[discarded_seed_id] = replacement
        selection_reasons[discarded_seed_id] = "backfill_next_best_eligible"
        selected_population_ids.add(replacement.population_id)
        source_usage[replacement.seed_id] += 1
        backfills.append(
            {
                "slot_seed_id": discarded_seed_id,
                "source_seed_id": replacement.seed_id,
                "source_population_id": replacement.population_id,
                "pass_count": replacement.pass_count,
            }
        )

    if len(selections) != len(CANONICAL_SEED_IDS):
        raise ReprojectionError("projection did not produce exactly 50 selected records")
    if any(usage > MAX_SOURCE_SEED_ROWS for usage in source_usage.values()):
        raise ReprojectionError("projection exceeded the source-seed backfill cap")

    rows = tuple(
        _projected_row(
            slot_seed_id,
            selections[slot_seed_id],
            selection_reason=selection_reasons[slot_seed_id],
        )
        for slot_seed_id in CANONICAL_SEED_IDS
    )
    base_report.update(
        {
            "status": "completed",
            "stop_reason": None,
            "discarded_seed_ids": discarded,
            "backfills": backfills,
            "selected_count": len(rows),
            "source_seed_use": dict(sorted(source_usage.items())),
            "selections": [
                {
                    "slot_seed_id": row["id"],
                    "source_seed_id": row["source_seed_id"],
                    "source_population_id": row["source_population_id"],
                    "pass_count": row["difficulty_pass_count"],
                    "selection_reason": row["selection_reason"],
                }
                for row in rows
            ],
        }
    )
    return ReprojectionResult(rows=rows, report=base_report, stopped=False)


def write_text_atomic(path: Path, content: str) -> None:
    """Durably replace a text artifact after its complete content is synced."""
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


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically write a canonical projection report."""
    write_text_atomic(path, json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")


def _pair_population_with_counts(
    population: Sequence[Mapping[str, Any]], counts: Sequence[Mapping[str, Any]]
) -> tuple[CountedRecord, ...]:
    by_id: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(population, start=1):
        population_id = row.get("id")
        seed_id = row.get("seed_id")
        if not isinstance(population_id, str) or not population_id:
            raise ReprojectionError(f"population record {index} has an invalid id")
        if population_id in by_id:
            raise ReprojectionError(f"population IDs are not unique: {population_id}")
        if seed_id not in CANONICAL_SEED_IDS:
            raise ReprojectionError(f"population {population_id} has an invalid seed_id")
        _validate_population_fields(row, population_id)
        by_id[population_id] = row

    count_by_id: dict[str, int] = {}
    for index, row in enumerate(counts, start=1):
        record_id = row.get("record_id")
        pass_count = row.get("pass_count")
        k = row.get("k")
        if not isinstance(record_id, str) or not record_id:
            raise ReprojectionError(f"pass-count record {index} has an invalid record_id")
        if record_id in count_by_id:
            raise ReprojectionError(f"pass-count IDs are not unique: {record_id}")
        if isinstance(pass_count, bool) or not isinstance(pass_count, int) or not 0 <= pass_count <= 8:
            raise ReprojectionError(f"pass-count record {record_id} has an invalid pass_count")
        if k != 8:
            raise ReprojectionError(f"pass-count record {record_id} must have k == 8")
        count_by_id[record_id] = pass_count

    if set(by_id) != set(count_by_id):
        missing = sorted(set(by_id) - set(count_by_id))
        extra = sorted(set(count_by_id) - set(by_id))
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing[:3]))
        if extra:
            details.append("unknown " + ", ".join(extra[:3]))
        raise ReprojectionError("population and pass counts differ: " + "; ".join(details))
    return tuple(
        CountedRecord(row=by_id[population_id], pass_count=count_by_id[population_id])
        for population_id in sorted(by_id)
    )


def _validate_population_fields(row: Mapping[str, Any], population_id: str) -> None:
    required_text = ("source_record_id", "source_kind", "question", "prompt", "schema_sql", "reference_sql")
    if not all(isinstance(row.get(field), str) and row[field].strip() for field in required_text):
        raise ReprojectionError(f"population {population_id} has an invalid text field")
    if not isinstance(row.get("expected_results"), list):
        raise ReprojectionError(f"population {population_id} has invalid expected_results")


def _reverify_reference_sql(records: Sequence[CountedRecord]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for record in records:
        verifier = NL2SQLVerifier(
            str(record.row["schema_sql"]), record.row["expected_results"]
        )
        breakdown = verifier.score_breakdown(
            str(record.row["prompt"]), str(record.row["reference_sql"])
        )
        if breakdown.final_score != 1.0:
            failures.append(
                {
                    "population_id": record.population_id,
                    "final_score": breakdown.final_score,
                    "failure_class": breakdown.failure_class,
                    "failure_detail": breakdown.failure_detail,
                }
            )
    return failures


def _select_for_seed(
    records: Sequence[CountedRecord],
) -> tuple[CountedRecord | None, str | None]:
    """Apply U1's preferred range, then its deterministic relaxed fallback."""
    preferred = [
        record
        for record in records
        if PREFERRED_MIN <= record.pass_count <= PREFERRED_MAX
    ]
    if preferred:
        return min(preferred, key=_selection_key), "nearest_two_preferred"
    relaxed = [
        record
        for record in records
        if RELAXED_MIN <= record.pass_count <= RELAXED_MAX
    ]
    if relaxed:
        return (
            min(
                relaxed,
                key=lambda record: (record.pass_count, record.population_id),
            ),
            "lowest_relaxed",
        )
    return None, None


def _sorted_eligible(records: Sequence[CountedRecord]) -> list[CountedRecord]:
    return sorted(
        (
            record
            for record in records
            if RELAXED_MIN <= record.pass_count <= RELAXED_MAX
        ),
        key=_selection_key,
    )


def _selection_key(record: CountedRecord) -> tuple[int, str]:
    if PREFERRED_MIN <= record.pass_count <= PREFERRED_MAX:
        return (abs(record.pass_count - TARGET_PASS_COUNT), record.population_id)
    return (record.pass_count + 10, record.population_id)


def _projected_row(
    slot_seed_id: str, source: CountedRecord, *, selection_reason: str
) -> dict[str, Any]:
    row = source.row
    return {
        "id": slot_seed_id,
        "seed_id": slot_seed_id,
        "source_seed_id": source.seed_id,
        "source_population_id": source.population_id,
        "source_record_id": row["source_record_id"],
        "source_kind": row["source_kind"],
        "selection_reason": selection_reason,
        "difficulty_pass_count": source.pass_count,
        "question": row["question"],
        "prompt": row["prompt"],
        "schema_sql": row["schema_sql"],
        "expected_results": row["expected_results"],
        "reference_sql": row["reference_sql"],
    }


def _base_report(
    *,
    population_path: Path,
    population_bytes: bytes,
    pass_counts_path: Path,
    count_bytes: bytes,
    record_count: int,
    verification_failures: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    source = (REPOSITORY_ROOT / "core" / "rewards" / "nl2sql.py").read_bytes()
    return {
        "schema_version": 1,
        "selection_rule_version": SELECTION_RULE_VERSION,
        "selection_rule": (
            "For each seed, select its pass-count record nearest to 2 in [1,4]; "
            "if none exists, select the lowest count in [1,6]; tie-break by population ID. "
            "Discard seeds with no [1,6] row and fill their slots from another seed's "
            "next-best unused eligible record, max two rows per source seed."
        ),
        "population": {
            "path": str(population_path),
            "sha256": hashlib.sha256(population_bytes).hexdigest(),
            "record_count": record_count,
        },
        "pass_counts": {
            "path": str(pass_counts_path),
            "sha256": hashlib.sha256(count_bytes).hexdigest(),
            "record_count": record_count,
            "k": 8,
        },
        "verifier": {
            "identity": "core.rewards.nl2sql.NL2SQLVerifier",
            "version": NL2SQLVerifier.VERSION,
            "source_sha256": hashlib.sha256(source).hexdigest(),
        },
        "reference_reverification": {
            "record_count": record_count,
            "full_pass_count": record_count - len(verification_failures),
            "failures": list(verification_failures),
        },
    }


def _load_jsonl(content: bytes, path: Path, *, label: str) -> list[dict[str, Any]]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ReprojectionError(f"{label} must be UTF-8 JSONL: {path}") from error
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ReprojectionError(f"{label} line {line_number} is not valid JSON") from error
        if not isinstance(value, Mapping):
            raise ReprojectionError(f"{label} line {line_number} must be a JSON object")
        rows.append(dict(value))
    if not rows:
        raise ReprojectionError(f"{label} has no records")
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


def _display_result(result: ReprojectionResult) -> dict[str, Any]:
    return {
        "status": result.report["status"],
        "selected_count": result.report["selected_count"],
        "discarded_seed_count": len(result.report["discarded_seed_ids"]),
        "backfill_count": len(result.report["backfills"]),
        "stop_reason": result.report["stop_reason"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
