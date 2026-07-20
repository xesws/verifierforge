"""Promote a small, factual D4 evidence subset into committed demo artifacts."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from app.api.report_projection import (
    ARENA_SELECTOR_VERSION,
    ReportEvidence,
    build_arena,
    build_savings_projection,
    projection_content_sha256,
    projection_sources,
)
from app.db import repository_gateway
from app.db.records import JobRecord
from core.contracts import (
    Control,
    Job,
    JobStatus,
    MetricRecord,
    Metrics,
    Report,
    ReportProjectionProvenance,
    ReportVerdict,
)


MAIN_JOB = "d4-m3-1p5b-r1-v0125"
CONTROL_JOB = "d4-m4-0p5b-random-v0126"
HELDOUT_REPORT = "artifacts/heldout/v0.12.7-report.json"
ARTIFACT_VERSION = "v0.32.3"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HELDOUT_DATASET = REPOSITORY_ROOT / "data/nl2sql/v0.10.0-heldout.jsonl"
BASELINE_SAMPLES = REPOSITORY_ROOT / "runs/p0-gate-a/v0.10.1-e2-heldout-samples.jsonl"
TUNED_SAMPLES = (
    REPOSITORY_ROOT
    / "runs/d4-m3-1p5b-r1-v0125/evidence/heldout-after-v0127/step_350/gate-a-samples.jsonl"
)


def build(
    *,
    runs_dir: Path,
    destination: Path,
    evidence: ReportEvidence | None = None,
) -> dict[str, Any]:
    """Create a replace-atomically demo dataset from the completed D4 evidence."""
    runs_dir = Path(runs_dir)
    destination = Path(destination)
    main_metrics = _read_metrics(runs_dir / MAIN_JOB / "metrics.jsonl")
    control_metrics = _read_metrics(runs_dir / CONTROL_JOB / "metrics.jsonl")
    heldout_path = runs_dir / MAIN_JOB / HELDOUT_REPORT
    heldout = _read_json(heldout_path)
    before = _triplet(heldout, "before")
    after = _triplet(heldout, "after")
    report_evidence = evidence or ReportEvidence(
        heldout_dataset=HELDOUT_DATASET,
        baseline_samples=BASELINE_SAMPLES,
        tuned_samples=TUNED_SAMPLES,
    )
    selection = build_arena(report_evidence)
    savings = build_savings_projection()
    generated_at = _generated_at(heldout)
    source_inputs = [
        (f"runs/{MAIN_JOB}/metrics.jsonl", runs_dir / MAIN_JOB / "metrics.jsonl"),
        (f"runs/{CONTROL_JOB}/metrics.jsonl", runs_dir / CONTROL_JOB / "metrics.jsonl"),
        (f"runs/{MAIN_JOB}/{HELDOUT_REPORT}", heldout_path),
        ("data/nl2sql/v0.10.0-heldout.jsonl", report_evidence.heldout_dataset),
        ("runs/p0-gate-a/v0.10.1-e2-heldout-samples.jsonl", report_evidence.baseline_samples),
        (
            "runs/d4-m3-1p5b-r1-v0125/evidence/heldout-after-v0127/"
            "step_350/gate-a-samples.jsonl",
            report_evidence.tuned_samples,
        ),
    ]
    sources = projection_sources(source_inputs)

    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    temporary.mkdir(parents=True, exist_ok=False)
    try:
        main_job = Job(
            job_id=MAIN_JOB,
            template="nl2sql-heldout",
            status=JobStatus.DONE,
            model="Qwen/Qwen2.5-1.5B-Instruct",
            created_at=main_metrics[0].timestamp,
            metrics=_metrics_shape(main_metrics),
            control=Control(pass_at_1=[record.pass_at_1 for record in control_metrics]),
            report=Report(
                baseline_pass_at_1=before["pass_at_1"],
                final_pass_at_1=after["pass_at_1"],
                control_final_pass_at_1=control_metrics[-1].pass_at_1,
                verdict=ReportVerdict.REAL_GAIN,
                narrative=(
                    "Held-out selection chose step 350 by maximum pass@1; the random-reward "
                    "control is included as a falsification reference, not proof of causality."
                ),
                projected_monthly_savings_usd=savings.projected_monthly_savings_usd,
                arena=selection.arena,
                savings_projection=savings,
                provenance=ReportProjectionProvenance(
                    artifact_version=ARTIFACT_VERSION,
                    s3_prefix=None,
                    generated_at=generated_at,
                    content_sha256="0" * 64,
                    sources=sources,
                ),
            ),
            endpoint=None,
        )
        projection_hash = projection_content_sha256(main_job.model_dump(mode="json"))
        assert main_job.report is not None and main_job.report.provenance is not None
        main_job = main_job.model_copy(
            update={
                "report": main_job.report.model_copy(
                    update={
                        "provenance": main_job.report.provenance.model_copy(
                            update={"content_sha256": projection_hash}
                        )
                    }
                )
            }
        )
        control_job = Job(
            job_id=CONTROL_JOB,
            template="nl2sql-random-reward-control",
            status=JobStatus.DONE,
            model="Qwen/Qwen2.5-0.5B-Instruct",
            created_at=control_metrics[0].timestamp,
            metrics=_metrics_shape(control_metrics),
            control=Control(pass_at_1=[]),
            report=None,
            endpoint=None,
        )
        _write_job(temporary, main_job, runs_dir / MAIN_JOB / "metrics.jsonl")
        _write_job(temporary, control_job, runs_dir / CONTROL_JOB / "metrics.jsonl")
        report_destination = temporary / "jobs" / MAIN_JOB / "heldout-report.json"
        report_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(heldout_path, report_destination)

        _write_json(
            temporary / "clusters.json",
            {
                "routing": {
                    "data-pull-sql": {
                        "cluster_id": "data-pull-sql",
                        "enabled": False,
                        "canary_percent": 0,
                        "target_model": "tuned",
                    }
                },
                "live_pass_rate": {"data-pull-sql": {"cluster_id": "data-pull-sql", "points": []}},
            },
        )
        index = {
            "schema_version": 2,
            "jobs": [
                {
                    "job_id": MAIN_JOB,
                    "job_path": f"jobs/{MAIN_JOB}/job.json",
                    "metrics_path": f"jobs/{MAIN_JOB}/metrics.jsonl",
                },
                {
                    "job_id": CONTROL_JOB,
                    "job_path": f"jobs/{CONTROL_JOB}/job.json",
                    "metrics_path": f"jobs/{CONTROL_JOB}/metrics.jsonl",
                },
            ],
            "clusters_path": "clusters.json",
        }
        _write_json(temporary / "index.json", index)
        _write_readme(temporary)
        _write_json(
            temporary / "manifest.json",
            {
                "schema_version": 2,
                "main_job": MAIN_JOB,
                "control_job": CONTROL_JOB,
                "heldout_before": before,
                "heldout_after": after,
                "arena_selector": {
                    "version": ARENA_SELECTOR_VERSION,
                    "sample_index": 1,
                    "quota": {"improved": 6, "both_pass": 2, "both_fail": 2},
                    "population_categories": selection.categories,
                    "selected_record_ids": selection.selected_record_ids,
                },
                "report_projection_sha256": projection_hash,
                "report_sources": [source.model_dump(mode="json") for source in sources],
                "files": _file_manifest(temporary),
            },
        )
        _replace_tree(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return _read_json(destination / "manifest.json")


def sync_job_projection(destination: Path) -> JobRecord:
    """Idempotently persist the artifact-derived flagship Job presentation."""
    job = Job.model_validate(_read_json(destination / "jobs" / MAIN_JOB / "job.json"))
    assert job.report is not None and job.report.provenance is not None

    async def write(repositories):
        existing = await repositories.jobs.get(job.job_id)
        record = JobRecord(
            job_id=job.job_id,
            template=job.template,
            status=job.status.value,
            config_json={"model": job.model} if existing is None else existing.config_json,
            created_at=job.created_at,
            s3_prefix=job.report.provenance.s3_prefix,
            summary_json={
                "job": job.model_dump(mode="json"),
                "projection": job.report.provenance.model_dump(mode="json"),
            },
        )
        if existing is not None:
            record = replace(record, created_at=existing.created_at)
        return await repositories.jobs.put(record)

    return repository_gateway().call(write)


def _read_metrics(path: Path) -> list[MetricRecord]:
    try:
        records = [MetricRecord.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except OSError as error:
        raise RuntimeError(f"missing source metrics: {path}") from error
    if not records:
        raise RuntimeError(f"source metrics are empty: {path}")
    return records


def _metrics_shape(records: list[MetricRecord]) -> Metrics:
    return Metrics(
        steps=[record.step for record in records],
        reward_mean=[record.reward_mean for record in records],
        pass_at_1=[record.pass_at_1 for record in records],
        entropy=[record.entropy for record in records],
    )


def _triplet(payload: Any, name: str) -> dict[str, float]:
    if not isinstance(payload, dict) or not isinstance(payload.get(name), dict):
        raise RuntimeError(f"held-out report is missing {name}")
    values = payload[name]
    try:
        return {field: float(values[field]) for field in ("pass_at_1", "pass_at_8", "mixed_fraction")}
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"held-out report has invalid {name} triplet") from error


def _generated_at(payload: dict[str, Any]) -> datetime:
    value = payload.get("generated_at")
    if not isinstance(value, str):
        raise RuntimeError("held-out report is missing generated_at")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError("held-out report has invalid generated_at") from error


def _write_job(root: Path, job: Job, source_metrics: Path) -> None:
    directory = root / "jobs" / job.job_id
    directory.mkdir(parents=True, exist_ok=True)
    _write_json(directory / "job.json", job.model_dump(mode="json"))
    shutil.copyfile(source_metrics, directory / "metrics.jsonl")


def _file_manifest(root: Path) -> list[dict[str, str | int]]:
    entries: list[dict[str, str | int]] = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        payload = path.read_bytes()
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return entries


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read JSON: {path}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON object expected: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_readme(root: Path) -> None:
    (root / "README.md").write_text(
        "# VerifierForge demo artifacts\n\n"
        "This directory contains reviewer-safe D4 metrics and a complete, derived held-out "
        "report projection. Its ten arena cards come from the frozen M5 evidence identified "
        "in manifest.json. It intentionally excludes model weights, checkpoints, credentials, "
        "raw traffic, and the full 60x8 sample evidence. "
        "Run `VF_API_DATA_MODE=artifacts uvicorn app.api.main:app` to serve it.\n",
        encoding="utf-8",
    )


def _replace_tree(temporary: Path, destination: Path) -> None:
    if destination.exists():
        previous = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.old")
        os.rename(destination, previous)
        try:
            os.rename(temporary, destination)
        except Exception:
            os.rename(previous, destination)
            raise
        shutil.rmtree(previous)
    else:
        os.rename(temporary, destination)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build committed reviewer demo artifacts from D4 evidence")
    parser.add_argument("--runs-dir", type=Path, default=Path(os.environ.get("VF_RUNS_DIR", "./runs")))
    parser.add_argument("--destination", type=Path, default=Path("data/demo-artifacts"))
    sync = parser.add_mutually_exclusive_group()
    sync.add_argument(
        "--sync-db",
        action="store_true",
        help="rebuild and idempotently backfill the configured relational Job store",
    )
    sync.add_argument(
        "--sync-existing-db",
        action="store_true",
        help="backfill the configured store from the committed artifact without rebuilding",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = (
        _read_json(args.destination / "manifest.json")
        if args.sync_existing_db
        else build(runs_dir=args.runs_dir, destination=args.destination)
    )
    if args.sync_db or args.sync_existing_db:
        from dotenv import load_dotenv

        load_dotenv(".env")
        sync_job_projection(args.destination)
    print(
        json.dumps(
            {
                "main_job": manifest["main_job"],
                "control_job": manifest["control_job"],
                "projection_sha256": manifest["report_projection_sha256"],
                "database_synced": args.sync_db or args.sync_existing_db,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
