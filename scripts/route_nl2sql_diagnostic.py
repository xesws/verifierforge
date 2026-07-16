#!/usr/bin/env python3
"""Derive an auditable Branch A/B route from immutable Gate A sample evidence."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Any


_RAW_FAILURE_CLASSES = (
    "parse_failure",
    "execution_error",
    "executable_not_full_pass",
)
_OPERATIONAL_FAILURE_CLASSES = (
    "format_parse_failure",
    "parse_failure",
    "execution_error",
    "executable_not_full_pass",
)
_LEADING_CODE_FENCE = re.compile(r"^\s*```(?:[A-Za-z0-9_+-]*)[ \t]*(?:\r?\n|$)")


def build_parser() -> argparse.ArgumentParser:
    """Build the offline routing command-line interface."""
    parser = argparse.ArgumentParser(
        description="Route immutable NL2SQL sample evidence to Branch A or B."
    )
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Write a route artifact and print the deterministic route summary."""
    args = build_parser().parse_args(argv)
    try:
        payload = route_sample_evidence(args.samples, args.evidence)
        write_route_artifact(args.output, payload)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"route_nl2sql_diagnostic error: {error}")
        return 2

    print(
        json.dumps(
            {
                "route": payload["route"],
                "D_format_parse_failure_fraction": payload["operational_taxonomy"][
                    "D_format_parse_failure_fraction"
                ],
                "failed_sample_count": payload["operational_taxonomy"][
                    "failed_sample_count"
                ],
            },
            sort_keys=True,
        )
    )
    return 0


def route_sample_evidence(samples_path: Path, evidence_path: Path) -> dict[str, Any]:
    """Build a hash-bound operational taxonomy from completed Gate A evidence."""
    sample_bytes = samples_path.read_bytes()
    evidence_bytes = evidence_path.read_bytes()
    source_evidence = _load_json_object(evidence_bytes, evidence_path)
    samples = _load_sample_rows(sample_bytes, samples_path)
    _validate_source_binding(source_evidence, sample_bytes, len(samples))

    raw_counts: Counter[str] = Counter()
    operational_counts: Counter[str] = Counter()
    format_examples: list[str] = []
    failed_count = 0
    for row in samples:
        if row["final_score"] == 1.0:
            continue
        failed_count += 1
        raw_class = row["failure_class"]
        raw_counts[raw_class] += 1
        operational_class = _operational_failure_class(row)
        operational_counts[operational_class] += 1
        if operational_class == "format_parse_failure" and len(format_examples) < 3:
            format_examples.append(row["completion"])

    _validate_raw_taxonomy(source_evidence, raw_counts, failed_count)
    if sum(operational_counts.values()) != failed_count:
        raise ValueError("operational taxonomy does not account for every failed sample")

    operational_categories = _categories(operational_counts, _OPERATIONAL_FAILURE_CLASSES, failed_count)
    derived_d = (
        operational_counts["format_parse_failure"] / failed_count if failed_count else 0.0
    )
    return {
        "schema_version": 1,
        "status": "completed",
        "timestamp": datetime.now(UTC).isoformat(),
        "source": {
            "sample_evidence": {
                "path": str(samples_path),
                "sha256": hashlib.sha256(sample_bytes).hexdigest(),
                "sample_count": len(samples),
            },
            "gate_a_evidence": {
                "path": str(evidence_path),
                "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
            },
        },
        "raw_taxonomy": {
            "failed_sample_count": failed_count,
            "categories": _categories(raw_counts, _RAW_FAILURE_CLASSES, failed_count),
        },
        "operational_taxonomy": {
            "failed_sample_count": failed_count,
            "categories": operational_categories,
            "D_format_parse_failure_fraction": derived_d,
            "format_parse_failure_completions": format_examples,
        },
        "route": "branch_a" if derived_d >= 0.50 else "branch_b",
    }


def write_route_artifact(path: Path, payload: Mapping[str, Any]) -> None:
    """Durably publish a route artifact without replacing a prior file early."""
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
            json.dump(dict(payload), temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _load_json_object(content: bytes, path: Path) -> Mapping[str, Any]:
    value = json.loads(content.decode("utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _load_sample_rows(content: bytes, path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(content.decode("utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        value = json.loads(raw_line)
        if not isinstance(value, Mapping):
            raise ValueError(f"{path} line {line_number} must be a JSON object")
        row = _validate_sample_row(value, path=path, line_number=line_number)
        rows.append(row)
    if not rows:
        raise ValueError(f"{path} contains no sample rows")
    return rows


def _validate_sample_row(
    row: Mapping[str, Any], *, path: Path, line_number: int
) -> dict[str, Any]:
    completion = row.get("completion")
    failure_class = row.get("failure_class")
    failure_detail = row.get("failure_detail")
    final_score = row.get("final_score")
    if not isinstance(completion, str):
        raise ValueError(f"{path} line {line_number} completion must be a string")
    if failure_class not in (*_RAW_FAILURE_CLASSES, "full_pass"):
        raise ValueError(f"{path} line {line_number} has an unknown failure_class")
    if failure_detail is not None and not isinstance(failure_detail, str):
        raise ValueError(f"{path} line {line_number} failure_detail must be a string or null")
    if not isinstance(final_score, (int, float)) or isinstance(final_score, bool):
        raise ValueError(f"{path} line {line_number} final_score must be numeric")
    return {
        "completion": completion,
        "failure_class": failure_class,
        "failure_detail": failure_detail,
        "final_score": float(final_score),
    }


def _validate_source_binding(
    evidence: Mapping[str, Any], sample_bytes: bytes, sample_count: int
) -> None:
    if evidence.get("status") != "completed":
        raise ValueError("source Gate A evidence must be completed")
    sample_evidence = evidence.get("sample_evidence")
    if not isinstance(sample_evidence, Mapping):
        raise ValueError("source Gate A evidence has no sample_evidence binding")
    if sample_evidence.get("sha256") != hashlib.sha256(sample_bytes).hexdigest():
        raise ValueError("source sample SHA-256 does not match Gate A evidence")
    if sample_evidence.get("sample_count") != sample_count:
        raise ValueError("source sample count does not match Gate A evidence")


def _validate_raw_taxonomy(
    evidence: Mapping[str, Any], counts: Counter[str], failed_count: int
) -> None:
    taxonomy = evidence.get("sample_taxonomy")
    if not isinstance(taxonomy, Mapping):
        raise ValueError("source Gate A evidence has no sample_taxonomy")
    if taxonomy.get("failed_sample_count") != failed_count:
        raise ValueError("raw failed sample count does not match Gate A evidence")
    categories = taxonomy.get("categories")
    if not isinstance(categories, Mapping):
        raise ValueError("source Gate A evidence has no raw categories")
    for failure_class in _RAW_FAILURE_CLASSES:
        source_category = categories.get(failure_class)
        if not isinstance(source_category, Mapping):
            raise ValueError(f"source Gate A evidence has no {failure_class} category")
        if source_category.get("count") != counts[failure_class]:
            raise ValueError(
                f"raw {failure_class} count does not match Gate A evidence"
            )


def _operational_failure_class(row: Mapping[str, Any]) -> str:
    """Classify only the documented fenced-SQL legacy-gate condition as format."""
    if (
        row["failure_detail"] == "not_single_read_only_statement"
        and _LEADING_CODE_FENCE.match(str(row["completion"]))
    ):
        return "format_parse_failure"
    return str(row["failure_class"])


def _categories(
    counts: Counter[str], classes: Sequence[str], failed_count: int
) -> dict[str, dict[str, float | int]]:
    return {
        failure_class: {
            "count": counts[failure_class],
            "fraction_of_failed": (
                counts[failure_class] / failed_count if failed_count else 0.0
            ),
        }
        for failure_class in classes
    }


if __name__ == "__main__":
    raise SystemExit(main())
