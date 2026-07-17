"""Promote a small, factual D4 evidence subset into committed demo artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from core.contracts import Control, Job, JobStatus, MetricRecord, Metrics, Report, ReportVerdict


MAIN_JOB = "d4-m3-1p5b-r1-v0125"
CONTROL_JOB = "d4-m4-0p5b-random-v0126"
HELDOUT_REPORT = "artifacts/heldout/v0.12.7-report.json"


def build(*, runs_dir: Path, destination: Path) -> dict[str, Any]:
    """Create a replace-atomically demo dataset from the completed D4 evidence."""
    runs_dir = Path(runs_dir)
    destination = Path(destination)
    main_metrics = _read_metrics(runs_dir / MAIN_JOB / "metrics.jsonl")
    control_metrics = _read_metrics(runs_dir / CONTROL_JOB / "metrics.jsonl")
    heldout_path = runs_dir / MAIN_JOB / HELDOUT_REPORT
    heldout = _read_json(heldout_path)
    before = _triplet(heldout, "before")
    after = _triplet(heldout, "after")

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
            ),
            endpoint=None,
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
            "schema_version": 1,
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
                "schema_version": 1,
                "main_job": MAIN_JOB,
                "control_job": CONTROL_JOB,
                "heldout_before": before,
                "heldout_after": after,
                "files": _file_manifest(temporary),
            },
        )
        _replace_tree(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return _read_json(destination / "manifest.json")


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
        "This directory contains reviewer-safe D4 metrics and held-out report metadata. "
        "It intentionally excludes model weights, checkpoints, credentials, and raw traffic. "
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build(runs_dir=args.runs_dir, destination=args.destination)
    print(json.dumps({"main_job": manifest["main_job"], "control_job": manifest["control_job"]}, sort_keys=True))


if __name__ == "__main__":
    main()
