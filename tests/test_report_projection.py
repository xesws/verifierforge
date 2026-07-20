from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.report_projection import (
    ReportEvidence,
    build_arena,
    build_savings_projection,
    projection_content_sha256,
)


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ReportEvidence(
    heldout_dataset=ROOT / "data/nl2sql/v0.10.0-heldout.jsonl",
    baseline_samples=ROOT / "runs/p0-gate-a/v0.10.1-e2-heldout-samples.jsonl",
    tuned_samples=(
        ROOT
        / "runs/d4-m3-1p5b-r1-v0125/evidence/heldout-after-v0127/"
        "step_350/gate-a-samples.jsonl"
    ),
)


def test_arena_selection_is_deterministic_balanced_and_heldout_only() -> None:
    first = build_arena(EVIDENCE)
    second = build_arena(EVIDENCE)

    assert first == second
    assert first.categories == {"improved": 12, "both_pass": 35, "both_fail": 13}
    assert first.arena.win_rate == 0.2
    assert len(first.arena.samples) == 10
    assert len(set(first.selected_record_ids)) == 10
    assert sum(
        sample.baseline_score < 1 and sample.tuned_score == 1
        for sample in first.arena.samples
    ) == 6
    assert sum(
        sample.baseline_score == sample.tuned_score == 1
        for sample in first.arena.samples
    ) == 2
    assert sum(
        sample.baseline_score < 1 and sample.tuned_score < 1
        for sample in first.arena.samples
    ) == 2


def test_arena_rejects_non_60_row_source(tmp_path: Path) -> None:
    record = json.loads(EVIDENCE.heldout_dataset.read_text().splitlines()[0])
    short = tmp_path / "heldout.jsonl"
    short.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="record IDs do not align"):
        build_arena(
            ReportEvidence(
                heldout_dataset=short,
                baseline_samples=EVIDENCE.baseline_samples,
                tuned_samples=EVIDENCE.tuned_samples,
            )
        )


def test_savings_and_projection_hash_are_explicit_and_stable() -> None:
    savings = build_savings_projection()
    payload = {
        "report": {
            "projected_monthly_savings_usd": savings.projected_monthly_savings_usd,
            "provenance": {"content_sha256": "a" * 64, "artifact_version": "v0.32.3"},
        }
    }
    changed_self_hash = json.loads(json.dumps(payload))
    changed_self_hash["report"]["provenance"]["content_sha256"] = "b" * 64

    assert savings.current_monthly_cost_usd == 5500.0
    assert savings.projected_monthly_cost_usd == 1650.0
    assert savings.projected_monthly_savings_usd == 3850.0
    assert projection_content_sha256(payload) == projection_content_sha256(changed_self_hash)
