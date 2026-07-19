"""Validation and deterministic reads for user-approved Agent samples."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

from core.contracts import ApprovedSampleSource, ApprovedSampleSourceKind


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPOSITORY_ROOT / "data"
REQUIRED_NL2SQL_FIELDS = {
    "id",
    "prompt",
    "reference_sql",
    "schema_sql",
    "expected_results",
}


class ApprovedSampleSourceError(ValueError):
    """An approved source no longer matches its governed identity."""


def data_root() -> Path:
    return Path(os.environ.get("VF_APPROVED_SAMPLE_ROOT", DEFAULT_DATA_ROOT)).expanduser().resolve()


def repository_uri_path(uri: str, *, root: Path | None = None) -> Path:
    """Resolve a repository URI while rejecting absolute paths and traversal."""

    relative = Path(uri)
    if relative.is_absolute():
        raise ApprovedSampleSourceError("sample source uri must be repository-relative")
    repository_root = REPOSITORY_ROOT.resolve()
    candidate = (repository_root / relative).resolve()
    allowed = (root or data_root()).resolve()
    if not candidate.is_relative_to(allowed):
        raise ApprovedSampleSourceError("sample source must remain under the approved data root")
    if candidate.suffix.lower() != ".jsonl" or not candidate.is_file():
        raise ApprovedSampleSourceError("sample source must be an existing JSONL file")
    return candidate


def inspect_repository_jsonl(uri: str) -> tuple[str, list[dict[str, Any]]]:
    """Return the byte identity and validated NL2SQL records for one source."""

    path = repository_uri_path(uri)
    body = path.read_bytes()
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(body.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ApprovedSampleSourceError(
                f"sample source line {line_number} is not valid JSON"
            ) from error
        if not isinstance(value, dict) or not REQUIRED_NL2SQL_FIELDS <= value.keys():
            raise ApprovedSampleSourceError(
                f"sample source line {line_number} is not a complete NL2SQL record"
            )
        sample_id = value["id"]
        if not isinstance(sample_id, str) or not sample_id or sample_id in seen_ids:
            raise ApprovedSampleSourceError(
                f"sample source line {line_number} has an invalid or duplicate id"
            )
        seen_ids.add(sample_id)
        records.append(value)
    if not records:
        raise ApprovedSampleSourceError("sample source must contain at least one record")
    return sha256(body).hexdigest(), records


def validate_approved_source(source: ApprovedSampleSource) -> list[dict[str, Any]]:
    if source.kind is not ApprovedSampleSourceKind.REPOSITORY_JSONL:
        raise ApprovedSampleSourceError("unsupported approved sample source kind")
    actual_sha256, records = inspect_repository_jsonl(source.uri)
    if actual_sha256 != source.sha256:
        raise ApprovedSampleSourceError("sample source SHA-256 does not match approval metadata")
    if len(records) != source.row_count:
        raise ApprovedSampleSourceError("sample source row count does not match approval metadata")
    return records

