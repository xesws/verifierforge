from scripts.export_nl2sql_review_sandbox import (
    MANIFEST_DEST,
    PROMPT_SCHEMA_DEST,
    SQL_DEST,
    exported_files,
)


def test_frontend_sql_sandbox_asset_matches_frozen_source() -> None:
    expected_sql, expected_prompt_schema, expected_manifest = exported_files()

    assert SQL_DEST.read_text(encoding="utf-8") == expected_sql
    assert PROMPT_SCHEMA_DEST.read_text(encoding="utf-8") == expected_prompt_schema
    assert MANIFEST_DEST.read_text(encoding="utf-8") == expected_manifest


def test_prompt_schema_contains_ddl_without_fixture_rows() -> None:
    prompt_schema = PROMPT_SCHEMA_DEST.read_text(encoding="utf-8")

    assert prompt_schema.count("CREATE TABLE ") == 4
    assert "department_id INTEGER NOT NULL" in prompt_schema
    assert "active INTEGER NOT NULL" in prompt_schema
    assert "INSERT INTO" not in prompt_schema
