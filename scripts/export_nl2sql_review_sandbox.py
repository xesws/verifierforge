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
PROMPT_SCHEMA_DEST = ROOT / "frontend/src/data/generated/nl2sql-review-schema.sql"
MANIFEST_DEST = ROOT / "frontend/src/data/generated/nl2sql-review-sandbox.json"
DATASET_ID = "nl2sql-v0.10.0-review-sandbox"


def _schema_only(schema_sql: str) -> str:
    statements = [statement.strip() for statement in schema_sql.split(";")]
    create_statements = [
        statement
        for statement in statements
        if statement.upper().startswith("CREATE TABLE ")
    ]
    if len(create_statements) != 4:
        raise ValueError("Frozen reviewer prompt requires exactly four CREATE TABLE statements")
    return ";\n\n".join(create_statements) + ";\n"


def exported_files() -> tuple[str, str, str]:
    source_bytes = SOURCE.read_bytes()
    records = [json.loads(line) for line in source_bytes.splitlines() if line.strip()]
    schemas = {record["schema_sql"] for record in records}
    if len(records) != 50 or len(schemas) != 1:
        raise ValueError(
            "Frozen reviewer sandbox requires 50 records with exactly one schema"
        )
    schema_sql = schemas.pop()
    prompt_schema = _schema_only(schema_sql)
    manifest = {
        "datasetId": DATASET_ID,
        "sourcePath": SOURCE.relative_to(ROOT).as_posix(),
        "sourceSha256": hashlib.sha256(source_bytes).hexdigest(),
        "schemaSha256": hashlib.sha256(schema_sql.encode()).hexdigest(),
        "promptSchemaSha256": hashlib.sha256(prompt_schema.encode()).hexdigest(),
        "sourceRowCount": len(records),
    }
    return schema_sql + "\n", prompt_schema, json.dumps(manifest, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected_sql, expected_prompt_schema, expected_manifest = exported_files()
    if args.check:
        if SQL_DEST.read_text(encoding="utf-8") != expected_sql:
            raise SystemExit(f"stale SQL sandbox asset: {SQL_DEST}")
        if PROMPT_SCHEMA_DEST.read_text(encoding="utf-8") != expected_prompt_schema:
            raise SystemExit(f"stale SQL prompt schema asset: {PROMPT_SCHEMA_DEST}")
        if MANIFEST_DEST.read_text(encoding="utf-8") != expected_manifest:
            raise SystemExit(f"stale SQL sandbox manifest: {MANIFEST_DEST}")
        return 0
    SQL_DEST.parent.mkdir(parents=True, exist_ok=True)
    SQL_DEST.write_text(expected_sql, encoding="utf-8")
    PROMPT_SCHEMA_DEST.write_text(expected_prompt_schema, encoding="utf-8")
    MANIFEST_DEST.write_text(expected_manifest, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
