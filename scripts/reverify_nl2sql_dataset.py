#!/usr/bin/env python3
"""Atomically re-verify stored NL2SQL reference SQL without any model call."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
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

from core.eval_runner import EvaluationRecordError, parse_evaluation_record  # noqa: E402
from core.rewards.nl2sql import NL2SQLVerifier  # noqa: E402


class ReverificationError(ValueError):
    """Raised when an input dataset cannot be safely re-verified."""


def build_parser() -> argparse.ArgumentParser:
    """Build the offline re-verification command-line interface."""
    parser = argparse.ArgumentParser(
        description="Re-verify every stored NL2SQL reference_sql and write evidence."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Publish evidence, returning one for any non-full candidate score."""
    args = build_parser().parse_args(argv)
    try:
        payload = reverify_dataset(args.input)
        write_evidence_atomic(args.output, payload)
    except (OSError, ReverificationError) as error:
        print(f"reverify_nl2sql_dataset error: {error}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "record_count": payload["record_count"],
                "full_pass_count": payload["full_pass_count"],
                "verifier_version": payload["verifier"]["version"],
            },
            sort_keys=True,
        )
    )
    return 0 if not payload["failures"] else 1


def reverify_dataset(input_path: Path) -> dict[str, Any]:
    """Score every `reference_sql` and return a deterministic audit payload."""
    raw = input_path.read_bytes()
    rows = _load_rows(raw, input_path)
    failures: list[dict[str, Any]] = []
    for record_index, row in enumerate(rows, start=1):
        try:
            record = parse_evaluation_record(row, source=f"{input_path} line {record_index}")
        except EvaluationRecordError as error:
            raise ReverificationError(str(error)) from error
        reference_sql = row.get("reference_sql")
        if not isinstance(reference_sql, str) or not reference_sql.strip():
            raise ReverificationError(
                f"{input_path} line {record_index} requires non-empty reference_sql"
            )
        verifier = NL2SQLVerifier(record.schema_sql, record.expected_results)
        breakdown = verifier.score_breakdown(record.prompt, reference_sql)
        if breakdown.final_score != 1.0:
            failures.append(
                {
                    "record_index": record_index,
                    "record_id": record.record_id,
                    "final_score": breakdown.final_score,
                    "failure_class": breakdown.failure_class,
                    "failure_detail": breakdown.failure_detail,
                }
            )

    source = (REPOSITORY_ROOT / "core" / "rewards" / "nl2sql.py").read_bytes()
    return {
        "schema_version": 1,
        "status": "completed",
        "timestamp": datetime.now(UTC).isoformat(),
        "input": {
            "path": str(input_path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "record_count": len(rows),
        },
        "verifier": {
            "identity": "core.rewards.nl2sql.NL2SQLVerifier",
            "version": NL2SQLVerifier.VERSION,
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "extraction_policy": "markdown_sql_fence_first_statement",
        },
        "record_count": len(rows),
        "full_pass_count": len(rows) - len(failures),
        "failures": failures,
    }


def write_evidence_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Durably replace evidence only after its complete content is synced."""
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


def _load_rows(raw: bytes, input_path: Path) -> list[dict[str, Any]]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ReverificationError(f"{input_path} must be UTF-8 JSONL") from error

    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ReverificationError(
                f"{input_path} line {line_number} is not valid JSON"
            ) from error
        if not isinstance(value, Mapping):
            raise ReverificationError(f"{input_path} line {line_number} must be a JSON object")
        rows.append(dict(value))
    if not rows:
        raise ReverificationError(f"{input_path} contains no records")
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
