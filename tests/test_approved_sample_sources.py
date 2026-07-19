from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from app.agent import sample_sources
from app.agent.sample_sources import (
    ApprovedSampleSourceError,
    inspect_repository_jsonl,
    repository_uri_path,
    validate_approved_source,
)
from core.contracts import ApprovedSampleSource


def _write_source(root: Path) -> Path:
    path = root / "samples.jsonl"
    record = {
        "id": "sample-1",
        "prompt": "query",
        "reference_sql": "SELECT 1",
        "schema_sql": "CREATE TABLE t (id INTEGER);",
        "expected_results": [[1]],
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return path


def test_source_identity_and_rows_are_validated(tmp_path: Path, monkeypatch) -> None:
    path = _write_source(tmp_path)
    monkeypatch.setattr(sample_sources, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setenv("VF_APPROVED_SAMPLE_ROOT", str(tmp_path))
    digest, rows = inspect_repository_jsonl("samples.jsonl")
    source = ApprovedSampleSource(
        kind="repository_jsonl",
        uri="samples.jsonl",
        sha256=digest,
        row_count=1,
        approved_by="owner",
        approved_at=datetime.now(timezone.utc),
    )

    assert validate_approved_source(source) == rows

    with pytest.raises(ApprovedSampleSourceError, match="SHA-256"):
        validate_approved_source(source.model_copy(update={"sha256": "0" * 64}))
    with pytest.raises(ApprovedSampleSourceError, match="row count"):
        validate_approved_source(source.model_copy(update={"row_count": 2}))


def test_source_path_cannot_escape_approved_root(tmp_path: Path, monkeypatch) -> None:
    allowed = tmp_path / "data"
    allowed.mkdir()
    (tmp_path / "outside.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(sample_sources, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setenv("VF_APPROVED_SAMPLE_ROOT", str(allowed))

    with pytest.raises(ApprovedSampleSourceError, match="approved data root"):
        repository_uri_path("outside.jsonl")
    with pytest.raises(ApprovedSampleSourceError, match="repository-relative"):
        repository_uri_path(str((allowed / "absolute.jsonl").resolve()))

