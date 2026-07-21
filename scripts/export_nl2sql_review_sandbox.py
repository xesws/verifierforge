#!/usr/bin/env python3
"""Export/check the browser's minimal frozen NL2SQL schema fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/nl2sql/v0.10.0-training-pool.jsonl"
SQL_DEST = ROOT / "frontend/src/data/generated/nl2sql-review-sandbox.sql"
MANIFEST_DEST = ROOT / "frontend/src/data/generated/nl2sql-review-sandbox.json"
DATASET_ID = "nl2sql-v0.10.0-review-sandbox"


def exported_files() -> tuple[str, str]:
    source_bytes = SOURCE.read_bytes()
    records = [json.loads(line) for line in source_bytes.splitlines() if line.strip()]
    schemas = {record["schema_sql"] for record in records}
    if len(records) != 50 or len(schemas) != 1:
        raise ValueError(
            "Frozen reviewer sandbox requires 50 records with exactly one schema"
        )
    schema_sql = schemas.pop()
    manifest = {
        "datasetId": DATASET_ID,
        "sourcePath": SOURCE.relative_to(ROOT).as_posix(),
        "sourceSha256": hashlib.sha256(source_bytes).hexdigest(),
        "schemaSha256": hashlib.sha256(schema_sql.encode()).hexdigest(),
        "sourceRowCount": len(records),
    }
    return schema_sql + "\n", json.dumps(manifest, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected_sql, expected_manifest = exported_files()
    if args.check:
        if SQL_DEST.read_text(encoding="utf-8") != expected_sql:
            raise SystemExit(f"stale SQL sandbox asset: {SQL_DEST}")
        if MANIFEST_DEST.read_text(encoding="utf-8") != expected_manifest:
            raise SystemExit(f"stale SQL sandbox manifest: {MANIFEST_DEST}")
        return 0
    SQL_DEST.parent.mkdir(parents=True, exist_ok=True)
    SQL_DEST.write_text(expected_sql, encoding="utf-8")
    MANIFEST_DEST.write_text(expected_manifest, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
