#!/usr/bin/env python3
"""Validate and publish U3's training/held-out/verifier freeze manifest."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from core.rewards.nl2sql import NL2SQLVerifier  # noqa: E402


TRAINING_RECORD_COUNT = 50
HELDOUT_RECORD_COUNT = 60
SAMPLES_PER_RECORD = 8
VERIFIER_PATH = Path("core/rewards/nl2sql.py")
VERIFIER_IDENTITY = "core.rewards.nl2sql.NL2SQLVerifier"
PASS_AT_1_MIN = 0.20
PASS_AT_1_MAX = 0.60
PASS_AT_8_MIN = 0.85
MIXED_FRACTION_MIN = 0.30


class FreezeError(ValueError):
    """Raised when requested U3 inputs cannot safely be frozen together."""


def build_parser() -> argparse.ArgumentParser:
    """Build the explicit command line for one three-piece publication."""
    parser = argparse.ArgumentParser(
        description="Validate U3 inputs and write a three-piece freeze manifest."
    )
    parser.add_argument("--training-pool", required=True, type=Path)
    parser.add_argument("--heldout", required=True, type=Path)
    parser.add_argument("--training-evidence", required=True, type=Path)
    parser.add_argument("--training-samples", required=True, type=Path)
    parser.add_argument("--heldout-evidence", required=True, type=Path)
    parser.add_argument("--heldout-samples", required=True, type=Path)
    parser.add_argument("--runtime-fixture", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Validate, then atomically publish the manifest and runtime alias."""
    args = build_parser().parse_args(argv)
    try:
        manifest = build_manifest(
            training_pool=args.training_pool,
            heldout=args.heldout,
            training_evidence=args.training_evidence,
            training_samples=args.training_samples,
            heldout_evidence=args.heldout_evidence,
            heldout_samples=args.heldout_samples,
            runtime_fixture=args.runtime_fixture,
        )
        publish_freeze(
            manifest_path=args.manifest,
            runtime_fixture=args.runtime_fixture,
            training_pool=args.training_pool,
            manifest=manifest,
        )
    except (FreezeError, OSError) as error:
        print(f"freeze_three_piece error: {error}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "heldout_count": HELDOUT_RECORD_COUNT,
                "manifest": _stable_path(args.manifest),
                "training_count": TRAINING_RECORD_COUNT,
                "verifier_version": NL2SQLVerifier.VERSION,
            },
            sort_keys=True,
        )
    )
    return 0


def build_manifest(
    *,
    training_pool: Path,
    heldout: Path,
    training_evidence: Path,
    training_samples: Path,
    heldout_evidence: Path,
    heldout_samples: Path,
    runtime_fixture: Path,
) -> dict[str, Any]:
    """Return a deterministic manifest after validating all freeze inputs."""
    training_rows, training_artifact = _read_jsonl_artifact(
        training_pool, "training pool"
    )
    heldout_rows, heldout_artifact = _read_jsonl_artifact(heldout, "held-out set")
    training_sources = _validate_dataset(
        training_rows,
        label="training pool",
        expected_count=TRAINING_RECORD_COUNT,
        expected_split="training",
    )
    heldout_sources = _validate_dataset(
        heldout_rows,
        label="held-out set",
        expected_count=HELDOUT_RECORD_COUNT,
        expected_split="held_out",
    )
    if training_sources & heldout_sources:
        raise FreezeError("training pool and held-out set source population IDs overlap")

    verifier = verifier_provenance()
    reference_check = _reverify_reference_sql(
        (*training_rows, *heldout_rows), verifier=verifier
    )
    if reference_check["full_pass_count"] != reference_check["record_count"]:
        raise FreezeError("training or held-out reference SQL did not score 1.0")

    training_report, training_report_artifact = _read_json_artifact(
        training_evidence, "training Gate A evidence"
    )
    heldout_report, heldout_report_artifact = _read_json_artifact(
        heldout_evidence, "held-out baseline evidence"
    )
    training_sample_artifact = _read_sample_artifact(
        training_samples, "training sample evidence"
    )
    heldout_sample_artifact = _read_sample_artifact(
        heldout_samples, "held-out sample evidence"
    )
    training_config = _validate_evidence(
        training_report,
        label="training Gate A evidence",
        dataset=training_artifact,
        expected_mode="gate",
        require_pass=True,
        expected_samples=TRAINING_RECORD_COUNT * SAMPLES_PER_RECORD,
        samples=training_sample_artifact,
        verifier=verifier,
    )
    heldout_config = _validate_evidence(
        heldout_report,
        label="held-out baseline evidence",
        dataset=heldout_artifact,
        expected_mode="reference",
        require_pass=False,
        expected_samples=HELDOUT_RECORD_COUNT * SAMPLES_PER_RECORD,
        samples=heldout_sample_artifact,
        verifier=verifier,
    )
    if training_config != heldout_config:
        raise FreezeError("training and held-out evidence use different eval configs")

    return {
        "schema_version": 1,
        "frozen_identities": {
            "heldout_evaluation_set": heldout_artifact,
            "training_pool": training_artifact,
            "verifier": verifier,
        },
        "runtime_training_fixture_alias": {
            "byte_count": training_artifact["byte_count"],
            "path": _stable_path(runtime_fixture),
            "record_count": training_artifact["record_count"],
            "sha256": training_artifact["sha256"],
            "source_identity": "training_pool",
        },
        "evidence": {
            "eval_config": training_config,
            "heldout_baseline": {
                "evidence": heldout_report_artifact,
                "metrics": _metric_snapshot(heldout_report),
                "samples": heldout_sample_artifact,
            },
            "training_gate": {
                "evidence": training_report_artifact,
                "metrics": _metric_snapshot(training_report),
                "samples": training_sample_artifact,
            },
        },
        "reference_reverification": reference_check,
        "zero_source_population_overlap": True,
        "git": {"source_commit": _git_output("rev-parse", "HEAD")},
    }


def publish_freeze(
    *,
    manifest_path: Path,
    runtime_fixture: Path,
    training_pool: Path,
    manifest: Mapping[str, Any],
) -> None:
    """Publish the byte-identical runtime alias and canonical manifest."""
    training_bytes = _read_bytes(training_pool, "training pool")
    alias = manifest.get("runtime_training_fixture_alias")
    if not isinstance(alias, Mapping) or alias.get("sha256") != _sha256(training_bytes):
        raise FreezeError("manifest does not bind the supplied training pool alias")
    write_bytes_atomic(runtime_fixture, training_bytes)
    if runtime_fixture.read_bytes() != training_bytes:
        raise FreezeError("runtime fixture is not byte-identical to training pool")
    write_json_atomic(manifest_path, manifest)


def verifier_provenance() -> dict[str, Any]:
    """Return the stable verifier v2 identity required by U3."""
    source = _read_bytes(REPOSITORY_ROOT / VERIFIER_PATH, "verifier source")
    return {
        "git_blob_id": _git_output("rev-parse", f"HEAD:{VERIFIER_PATH.as_posix()}"),
        "identity": VERIFIER_IDENTITY,
        "source_path": VERIFIER_PATH.as_posix(),
        "source_sha256": _sha256(source),
        "version": NL2SQLVerifier.VERSION,
    }


def _validate_dataset(
    rows: Sequence[Mapping[str, Any]],
    *,
    label: str,
    expected_count: int,
    expected_split: str,
) -> set[str]:
    if len(rows) != expected_count:
        raise FreezeError(f"{label} must contain exactly {expected_count} records")
    identifiers: set[str] = set()
    source_ids: set[str] = set()
    required = (
        "id",
        "source_population_id",
        "prompt",
        "schema_sql",
        "expected_results",
        "reference_sql",
    )
    for index, row in enumerate(rows, start=1):
        if row.get("split") != expected_split:
            raise FreezeError(f"{label} record {index} has an invalid split marker")
        if not all(isinstance(row.get(field), str) and row[field] for field in required[:4]):
            raise FreezeError(f"{label} record {index} has an invalid text field")
        if not isinstance(row.get("expected_results"), list):
            raise FreezeError(f"{label} record {index} has invalid expected_results")
        if not isinstance(row.get("reference_sql"), str) or not row["reference_sql"]:
            raise FreezeError(f"{label} record {index} has invalid reference_sql")
        identifier = str(row["id"])
        source_id = str(row["source_population_id"])
        if identifier in identifiers:
            raise FreezeError(f"{label} record IDs are not unique")
        if source_id in source_ids:
            raise FreezeError(f"{label} source population IDs are not unique")
        identifiers.add(identifier)
        source_ids.add(source_id)
    return source_ids


def _reverify_reference_sql(
    rows: Sequence[Mapping[str, Any]], *, verifier: Mapping[str, Any]
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    for row in rows:
        scorer = NL2SQLVerifier(row["schema_sql"], row["expected_results"])
        breakdown = scorer.score_breakdown(row["prompt"], row["reference_sql"])
        if breakdown.final_score != 1.0:
            failures.append(
                {
                    "id": row["id"],
                    "failure_class": breakdown.failure_class,
                    "final_score": breakdown.final_score,
                }
            )
    return {
        "failures": failures,
        "full_pass_count": len(rows) - len(failures),
        "record_count": len(rows),
        "verifier_version": verifier["version"],
    }


def _validate_evidence(
    evidence: Mapping[str, Any],
    *,
    label: str,
    dataset: Mapping[str, Any],
    expected_mode: str,
    require_pass: bool,
    expected_samples: int,
    samples: Mapping[str, Any],
    verifier: Mapping[str, Any],
) -> dict[str, str]:
    if evidence.get("status") != "completed" or evidence.get("mode") != expected_mode:
        raise FreezeError(f"{label} is not completed {expected_mode} evidence")
    if evidence.get("k") != SAMPLES_PER_RECORD:
        raise FreezeError(f"{label} must use k == {SAMPLES_PER_RECORD}")
    if evidence.get("input_sha256") != dataset["sha256"]:
        raise FreezeError(f"{label} input_sha256 does not match its dataset")
    if evidence.get("candidate_count") != dataset["record_count"]:
        raise FreezeError(f"{label} candidate_count does not match its dataset")
    if evidence.get("sample_count") != expected_samples:
        raise FreezeError(f"{label} sample_count is invalid")
    _validate_evidence_verifier(evidence, label=label, verifier=verifier)
    saved_samples = evidence.get("sample_evidence")
    if not isinstance(saved_samples, Mapping):
        raise FreezeError(f"{label} lacks sample evidence")
    if (
        saved_samples.get("sha256") != samples["sha256"]
        or saved_samples.get("sample_count") != samples["record_count"]
        or samples["record_count"] != expected_samples
    ):
        raise FreezeError(f"{label} sample evidence does not match its JSONL")
    config = _config_identity(evidence, label=label)
    if require_pass:
        _validate_training_admission(evidence, label=label)
    return config


def _validate_evidence_verifier(
    evidence: Mapping[str, Any], *, label: str, verifier: Mapping[str, Any]
) -> None:
    evidence_verifier = evidence.get("verifier")
    if not isinstance(evidence_verifier, Mapping):
        raise FreezeError(f"{label} lacks verifier provenance")
    for field in ("identity", "version", "source_sha256"):
        if evidence_verifier.get(field) != verifier[field]:
            raise FreezeError(f"{label} verifier {field} does not match U3")


def _config_identity(evidence: Mapping[str, Any], *, label: str) -> dict[str, str]:
    config = evidence.get("resolved_config")
    if not isinstance(config, Mapping):
        raise FreezeError(f"{label} lacks resolved eval config")
    model = config.get("model")
    base_url = config.get("base_url")
    if not isinstance(model, str) or not model or not isinstance(base_url, str) or not base_url:
        raise FreezeError(f"{label} has invalid resolved eval config")
    return {"base_url": base_url, "model": model}


def _validate_training_admission(evidence: Mapping[str, Any], *, label: str) -> None:
    if evidence.get("passed") is not True:
        raise FreezeError(f"{label} must have passed == true")
    pass_at_1 = _number(evidence.get("pass_at_1"), f"{label} pass_at_1")
    pass_at_8 = _number(evidence.get("pass_at_8"), f"{label} pass_at_8")
    mixed_fraction = _number(evidence.get("mixed_fraction"), f"{label} mixed_fraction")
    if not (
        PASS_AT_1_MIN <= pass_at_1 <= PASS_AT_1_MAX
        and pass_at_8 >= PASS_AT_8_MIN
        and mixed_fraction >= MIXED_FRACTION_MIN
    ):
        raise FreezeError(f"{label} does not satisfy fixed U2 thresholds")


def _metric_snapshot(evidence: Mapping[str, Any]) -> dict[str, float]:
    return {
        "mixed_fraction": _number(evidence.get("mixed_fraction"), "mixed_fraction"),
        "pass_at_1": _number(evidence.get("pass_at_1"), "pass_at_1"),
        "pass_at_8": _number(evidence.get("pass_at_8"), "pass_at_8"),
    }


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FreezeError(f"{label} must be numeric")
    return float(value)


def _read_jsonl_artifact(path: Path, label: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = _read_bytes(path, label)
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise FreezeError(f"{label} must be UTF-8 JSONL") from error
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise FreezeError(f"{label} line {line_number} is not valid JSON") from error
        if not isinstance(value, Mapping):
            raise FreezeError(f"{label} line {line_number} must be a JSON object")
        rows.append(dict(value))
    if not rows:
        raise FreezeError(f"{label} contains no records")
    return rows, _artifact_descriptor(path, raw, record_count=len(rows))


def _read_sample_artifact(path: Path, label: str) -> dict[str, Any]:
    _, artifact = _read_jsonl_artifact(path, label)
    return artifact


def _read_json_artifact(path: Path, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = _read_bytes(path, label)
    try:
        value = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise FreezeError(f"{label} must be UTF-8 JSON") from error
    except json.JSONDecodeError as error:
        raise FreezeError(f"{label} is not valid JSON") from error
    if not isinstance(value, Mapping):
        raise FreezeError(f"{label} must be a JSON object")
    return dict(value), _artifact_descriptor(path, raw, record_count=1)


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as error:
        raise FreezeError(f"{label} does not exist: {path}") from error
    except OSError as error:
        raise FreezeError(f"cannot read {label}: {path}") from error


def _artifact_descriptor(path: Path, raw: bytes, *, record_count: int) -> dict[str, Any]:
    return {
        "byte_count": len(raw),
        "path": _stable_path(path),
        "record_count": record_count,
        "sha256": _sha256(raw),
    }


def _stable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return path.name


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise FreezeError(f"git {' '.join(args)} failed")
    value = completed.stdout.strip()
    if not value:
        raise FreezeError(f"git {' '.join(args)} returned no value")
    return value


def write_bytes_atomic(path: Path, content: bytes) -> None:
    """Atomically publish arbitrary immutable artifact bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically publish canonical, deterministic manifest JSON."""
    write_bytes_atomic(path, json.dumps(dict(payload), indent=2, sort_keys=True).encode() + b"\n")


if __name__ == "__main__":
    raise SystemExit(main())
