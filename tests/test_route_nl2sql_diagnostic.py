from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import route_nl2sql_diagnostic as routing


def _row(
    completion: str,
    *,
    failure_class: str,
    failure_detail: str | None,
    final_score: float,
) -> dict[str, object]:
    return {
        "completion": completion,
        "failure_class": failure_class,
        "failure_detail": failure_detail,
        "final_score": final_score,
    }


def _write_sources(tmp_path: Path, rows: list[dict[str, object]]) -> tuple[Path, Path]:
    samples = tmp_path / "samples.jsonl"
    samples.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    counts = {
        name: sum(
            row["failure_class"] == name and row["final_score"] != 1.0 for row in rows
        )
        for name in ("parse_failure", "execution_error", "executable_not_full_pass")
    }
    failed_count = sum(row["final_score"] != 1.0 for row in rows)
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "status": "completed",
                "sample_evidence": {
                    "sha256": hashlib.sha256(samples.read_bytes()).hexdigest(),
                    "sample_count": len(rows),
                },
                "sample_taxonomy": {
                    "failed_sample_count": failed_count,
                    "categories": {
                        name: {"count": count} for name, count in counts.items()
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return samples, evidence


def test_route_preserves_raw_taxonomy_and_routes_fenced_sql_to_branch_a(
    tmp_path: Path,
) -> None:
    rows = [
        _row("SELECT 1", failure_class="full_pass", failure_detail=None, final_score=1.0),
        _row(
            "```sql\nSELECT 1;\n```",
            failure_class="execution_error",
            failure_detail="not_single_read_only_statement",
            final_score=0.2,
        ),
        _row(
            "```SQL\nSELECT 2;\n```",
            failure_class="execution_error",
            failure_detail="not_single_read_only_statement",
            final_score=0.2,
        ),
        _row(
            "SELECT missing FROM people",
            failure_class="execution_error",
            failure_detail="sqlite_execution_error",
            final_score=0.2,
        ),
        _row(
            "SELECT nobody FROM people",
            failure_class="executable_not_full_pass",
            failure_detail="result_mismatch",
            final_score=0.5,
        ),
    ]
    samples, evidence = _write_sources(tmp_path, rows)

    payload = routing.route_sample_evidence(samples, evidence)

    assert payload["route"] == "branch_a"
    assert payload["raw_taxonomy"]["categories"]["execution_error"]["count"] == 3
    taxonomy = payload["operational_taxonomy"]
    assert taxonomy["failed_sample_count"] == 4
    assert taxonomy["categories"]["format_parse_failure"] == {
        "count": 2,
        "fraction_of_failed": 0.5,
    }
    assert taxonomy["D_format_parse_failure_fraction"] == 0.5
    assert taxonomy["format_parse_failure_completions"] == [
        "```sql\nSELECT 1;\n```",
        "```SQL\nSELECT 2;\n```",
    ]
    assert payload["source"]["sample_evidence"]["sha256"] == hashlib.sha256(
        samples.read_bytes()
    ).hexdigest()


def test_non_fenced_legacy_gate_failure_remains_an_execution_error(tmp_path: Path) -> None:
    rows = [
        _row(
            "Explanation: SELECT 1",
            failure_class="execution_error",
            failure_detail="not_single_read_only_statement",
            final_score=0.2,
        )
    ]
    samples, evidence = _write_sources(tmp_path, rows)

    payload = routing.route_sample_evidence(samples, evidence)

    assert payload["route"] == "branch_b"
    assert payload["operational_taxonomy"]["categories"]["format_parse_failure"]["count"] == 0
    assert payload["operational_taxonomy"]["categories"]["execution_error"]["count"] == 1


def test_route_rejects_source_evidence_with_mismatched_raw_counts(tmp_path: Path) -> None:
    rows = [
        _row(
            "```sql\nSELECT 1;\n```",
            failure_class="execution_error",
            failure_detail="not_single_read_only_statement",
            final_score=0.2,
        )
    ]
    samples, evidence = _write_sources(tmp_path, rows)
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    payload["sample_taxonomy"]["categories"]["execution_error"]["count"] = 2
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="raw execution_error count"):
        routing.route_sample_evidence(samples, evidence)


def test_route_artifact_keeps_prior_file_when_atomic_publish_fails(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "route.json"
    output.write_text('{"previous": true}\n', encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("simulated publish failure")

    monkeypatch.setattr(routing.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated publish failure"):
        routing.write_route_artifact(output, {"route": "branch_a"})

    assert output.read_text(encoding="utf-8") == '{"previous": true}\n'
    assert not list(tmp_path.glob(".route.json.*"))
