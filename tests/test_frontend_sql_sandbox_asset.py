from scripts.export_nl2sql_review_sandbox import (
    MANIFEST_DEST,
    SQL_DEST,
    exported_files,
)


def test_frontend_sql_sandbox_asset_matches_frozen_source() -> None:
    expected_sql, expected_manifest = exported_files()

    assert SQL_DEST.read_text(encoding="utf-8") == expected_sql
    assert MANIFEST_DEST.read_text(encoding="utf-8") == expected_manifest
