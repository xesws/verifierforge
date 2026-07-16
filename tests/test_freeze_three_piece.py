from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import freeze_three_piece as freeze


SCHEMA = """
CREATE TABLE values_table (value INTEGER NOT NULL);
INSERT INTO values_table VALUES (1);
"""


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _dataset_rows(count: int, *, prefix: str, split: str) -> list[dict[str, object]]:
    return [
        {
            "difficulty_pass_count": 2,
            "expected_results": [[1]],
            "id": f"{prefix}-{index:03d}",
            "prompt": f"Return the value for {prefix}-{index:03d}.",
            "reference_sql": "SELECT value FROM values_table",
            "schema_sql": SCHEMA,
            "source_population_id": f"source:{prefix}-{index:03d}",
            "split": split,
        }
        for index in range(1, count + 1)
    ]


def _write_samples(path: Path, count: int) -> str:
    _write_jsonl(
        path,
        [
            {
                "completion": "SELECT value FROM values_table",
                "record_id": f"record-{index:03d}",
                "request_ordinal": index,
            }
            for index in range(1, count + 1)
        ],
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence(
    dataset: Path,
    samples: Path,
    *,
    mode: str,
    passed: bool,
    record_count: int,
) -> dict[str, object]:
    sample_count = record_count * freeze.SAMPLES_PER_RECORD
    verifier = freeze.verifier_provenance()
    return {
        "candidate_count": record_count,
        "input_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "k": freeze.SAMPLES_PER_RECORD,
        "mixed_fraction": 0.84 if mode == "gate" else 0.46,
        "mode": mode,
        "pass_at_1": 0.28 if mode == "gate" else 0.58,
        "pass_at_8": 0.88 if mode == "gate" else 0.76,
        "passed": passed,
        "resolved_config": {
            "base_url": "http://127.0.0.1:8000/v1",
            "model": "Qwen2.5-1.5B-Instruct",
        },
        "sample_count": sample_count,
        "sample_evidence": {
            "sample_count": sample_count,
            "sha256": hashlib.sha256(samples.read_bytes()).hexdigest(),
        },
        "status": "completed",
        "verifier": {
            "identity": verifier["identity"],
            "source_sha256": verifier["source_sha256"],
            "version": verifier["version"],
        },
    }


def _inputs(tmp_path: Path) -> dict[str, Path]:
    training = tmp_path / "training.jsonl"
    heldout = tmp_path / "heldout.jsonl"
    training_samples = tmp_path / "training-samples.jsonl"
    heldout_samples = tmp_path / "heldout-samples.jsonl"
    training_evidence = tmp_path / "training-evidence.json"
    heldout_evidence = tmp_path / "heldout-evidence.json"
    _write_jsonl(
        training,
        _dataset_rows(freeze.TRAINING_RECORD_COUNT, prefix="training", split="training"),
    )
    _write_jsonl(
        heldout,
        _dataset_rows(freeze.HELDOUT_RECORD_COUNT, prefix="heldout", split="held_out"),
    )
    _write_samples(training_samples, freeze.TRAINING_RECORD_COUNT * freeze.SAMPLES_PER_RECORD)
    _write_samples(heldout_samples, freeze.HELDOUT_RECORD_COUNT * freeze.SAMPLES_PER_RECORD)
    _write_json(
        training_evidence,
        _evidence(
            training,
            training_samples,
            mode="gate",
            passed=True,
            record_count=freeze.TRAINING_RECORD_COUNT,
        ),
    )
    _write_json(
        heldout_evidence,
        _evidence(
            heldout,
            heldout_samples,
            mode="reference",
            passed=False,
            record_count=freeze.HELDOUT_RECORD_COUNT,
        ),
    )
    return {
        "heldout": heldout,
        "heldout_evidence": heldout_evidence,
        "heldout_samples": heldout_samples,
        "runtime_fixture": tmp_path / "nl2sql_v1.jsonl",
        "training_evidence": training_evidence,
        "training_pool": training,
        "training_samples": training_samples,
    }


def test_manifest_binds_exactly_three_identities_and_evidence(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)

    manifest = freeze.build_manifest(**inputs)

    assert set(manifest["frozen_identities"]) == {
        "training_pool",
        "heldout_evaluation_set",
        "verifier",
    }
    assert manifest["frozen_identities"]["training_pool"]["record_count"] == 50
    assert manifest["frozen_identities"]["heldout_evaluation_set"]["record_count"] == 60
    assert manifest["runtime_training_fixture_alias"]["sha256"] == manifest[
        "frozen_identities"
    ]["training_pool"]["sha256"]
    assert manifest["reference_reverification"] == {
        "failures": [],
        "full_pass_count": 110,
        "record_count": 110,
        "verifier_version": 2,
    }
    assert manifest["evidence"]["training_gate"]["metrics"]["pass_at_8"] == 0.88
    assert manifest["evidence"]["heldout_baseline"]["metrics"]["pass_at_1"] == 0.58


def test_manifest_rejects_source_overlap_before_freeze(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    rows = [json.loads(line) for line in inputs["heldout"].read_text().splitlines()]
    rows[0]["source_population_id"] = "source:training-001"
    _write_jsonl(inputs["heldout"], rows)

    with pytest.raises(freeze.FreezeError, match="source population IDs overlap"):
        freeze.build_manifest(**inputs)


def test_manifest_rejects_training_evidence_that_no_longer_passes(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    evidence = json.loads(inputs["training_evidence"].read_text())
    evidence["passed"] = False
    _write_json(inputs["training_evidence"], evidence)

    with pytest.raises(freeze.FreezeError, match="passed == true"):
        freeze.build_manifest(**inputs)


def test_cli_publishes_byte_identical_runtime_fixture_and_manifest(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    arguments = [
        "--training-pool",
        str(inputs["training_pool"]),
        "--heldout",
        str(inputs["heldout"]),
        "--training-evidence",
        str(inputs["training_evidence"]),
        "--training-samples",
        str(inputs["training_samples"]),
        "--heldout-evidence",
        str(inputs["heldout_evidence"]),
        "--heldout-samples",
        str(inputs["heldout_samples"]),
        "--runtime-fixture",
        str(inputs["runtime_fixture"]),
        "--manifest",
        str(manifest_path),
    ]

    assert freeze.main(arguments) == 0
    assert inputs["runtime_fixture"].read_bytes() == inputs["training_pool"].read_bytes()
    payload = json.loads(manifest_path.read_text())
    assert payload["runtime_training_fixture_alias"]["sha256"] == hashlib.sha256(
        inputs["runtime_fixture"].read_bytes()
    ).hexdigest()


def test_atomic_write_keeps_prior_complete_fixture_on_publish_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "fixture.jsonl"
    output.write_bytes(b"previous-complete\n")

    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("simulated publish failure")

    monkeypatch.setattr(freeze.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated publish failure"):
        freeze.write_bytes_atomic(output, b"new-content\n")

    assert output.read_bytes() == b"previous-complete\n"
    assert not list(tmp_path.glob(".fixture.jsonl.*"))
