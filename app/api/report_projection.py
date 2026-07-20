"""Deterministically derive the reviewer report from frozen D4 evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from core.contracts import (
    Arena,
    ArenaSample,
    ReportProjectionSource,
    SavingsProjection,
)


ARENA_SELECTOR_VERSION = "vf-arena-v1"
CURRENT_MONTHLY_COST_USD = 5_500.0
TUNED_COST_FRACTION = 0.30


@dataclass(frozen=True)
class ReportEvidence:
    heldout_dataset: Path
    baseline_samples: Path
    tuned_samples: Path


@dataclass(frozen=True)
class ArenaSelection:
    arena: Arena
    selected_record_ids: list[str]
    categories: dict[str, int]


def build_arena(evidence: ReportEvidence) -> ArenaSelection:
    heldout = _unique_jsonl(evidence.heldout_dataset, "id")
    baseline = _sample_one(evidence.baseline_samples)
    tuned = _sample_one(evidence.tuned_samples)
    expected_ids = set(heldout)
    if set(baseline) != expected_ids or set(tuned) != expected_ids:
        raise RuntimeError("held-out and sample evidence record IDs do not align")
    if len(expected_ids) != 60:
        raise RuntimeError("arena evidence must contain exactly 60 held-out records")

    strata: dict[str, list[str]] = {
        "improved": [],
        "both_pass": [],
        "both_fail": [],
    }
    for record_id in expected_ids:
        baseline_pass = _score(baseline[record_id]) == 1.0
        tuned_pass = _score(tuned[record_id]) == 1.0
        if not baseline_pass and tuned_pass:
            strata["improved"].append(record_id)
        elif baseline_pass and tuned_pass:
            strata["both_pass"].append(record_id)
        elif not baseline_pass and not tuned_pass:
            strata["both_fail"].append(record_id)

    quotas = {"improved": 6, "both_pass": 2, "both_fail": 2}
    selected: list[str] = []
    for category, quota in quotas.items():
        ordered = sorted(strata[category], key=lambda value: (_selector_hash(value), value))
        if len(ordered) < quota:
            raise RuntimeError(f"arena evidence lacks {category} examples")
        selected.extend(ordered[:quota])

    samples = [
        ArenaSample(
            prompt=_question(heldout[record_id]),
            baseline_output=_completion(baseline[record_id]),
            tuned_output=_completion(tuned[record_id]),
            baseline_score=_score(baseline[record_id]),
            tuned_score=_score(tuned[record_id]),
        )
        for record_id in selected
    ]
    return ArenaSelection(
        arena=Arena(
            win_rate=len(strata["improved"]) / len(expected_ids),
            samples=samples,
        ),
        selected_record_ids=selected,
        categories={name: len(values) for name, values in strata.items()},
    )


def build_savings_projection() -> SavingsProjection:
    projected = CURRENT_MONTHLY_COST_USD * TUNED_COST_FRACTION
    return SavingsProjection(
        current_monthly_cost_usd=CURRENT_MONTHLY_COST_USD,
        projected_monthly_cost_usd=projected,
        projected_monthly_savings_usd=CURRENT_MONTHLY_COST_USD - projected,
        formula=(
            "projected_monthly_savings_usd = current_monthly_cost_usd - "
            "(current_monthly_cost_usd * 0.30)"
        ),
        assumptions=[
            "Current monthly cost is the data-pull-sql Discover product fact for 95,000 calls.",
            "Tuned inference is estimated at 30% of the current recurring workflow cost.",
            "One-time training and provisioning costs are excluded from recurring savings.",
        ],
    )


def projection_sources(inputs: list[tuple[str, Path]]) -> list[ReportProjectionSource]:
    return [
        ReportProjectionSource(path=logical_path, sha256=_sha256(path))
        for logical_path, path in inputs
    ]


def projection_content_sha256(payload: dict[str, Any]) -> str:
    """Hash canonical projection JSON while excluding the self-referential hash."""
    copied = json.loads(json.dumps(payload))
    report = copied.get("report")
    if isinstance(report, dict):
        provenance = report.get("provenance")
        if isinstance(provenance, dict):
            provenance.pop("content_sha256", None)
    encoded = json.dumps(copied, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _sample_one(path: Path) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for record in _jsonl(path):
        if record.get("sample_index") != 1:
            continue
        record_id = record.get("record_id")
        if not isinstance(record_id, str) or not record_id:
            raise RuntimeError(f"sample evidence has invalid record_id: {path}")
        if record_id in selected:
            raise RuntimeError(f"duplicate first sample for {record_id}: {path}")
        selected[record_id] = record
    return selected


def _unique_jsonl(path: Path, key: str) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for record in _jsonl(path):
        identity = record.get(key)
        if not isinstance(identity, str) or not identity:
            raise RuntimeError(f"JSONL record has invalid {key}: {path}")
        if identity in records:
            raise RuntimeError(f"duplicate {key} {identity!r}: {path}")
        records[identity] = record
    return records


def _jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines if line.strip()]
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read evidence JSONL: {path}") from error
    if not all(isinstance(record, dict) for record in records):
        raise RuntimeError(f"evidence JSONL must contain objects: {path}")
    return records


def _selector_hash(record_id: str) -> str:
    return hashlib.sha256(f"{ARENA_SELECTOR_VERSION}\0{record_id}".encode()).hexdigest()


def _score(record: dict[str, Any]) -> float:
    try:
        return float(record["final_score"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("sample evidence has invalid final_score") from error


def _completion(record: dict[str, Any]) -> str:
    value = record.get("completion")
    if not isinstance(value, str):
        raise RuntimeError("sample evidence has invalid completion")
    return value


def _question(record: dict[str, Any]) -> str:
    value = record.get("question")
    if not isinstance(value, str) or not value:
        raise RuntimeError("held-out record has invalid question")
    return value


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise RuntimeError(f"cannot hash report source: {path}") from error
