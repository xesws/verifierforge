from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.storage.local import LocalStorage
from trainer.m6_archive import M6ArchiveError, create_archive, sha256_file


def _runtime_evidence(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[pip_freeze]\nverl==0.8.0\n[nvidia_smi]\nH100, 570.195.03\n", encoding="utf-8")


def _serveable_export(native: Path) -> Path:
    export = native / "actor" / "serveable_huggingface"
    export.mkdir(parents=True)
    (export / "config.json").write_text("{}", encoding="utf-8")
    (export / "model.safetensors.index.json").write_text("{}", encoding="utf-8")
    (export / "model.safetensors").write_bytes(b"weights")
    (export / "verifierforge-serveable.json").write_text("{}", encoding="utf-8")
    return export


def _write_completed_pair(storage: LocalStorage) -> tuple[str, str]:
    main_job = "m3"
    control_job = "m4"
    main = storage.root / main_job
    control = storage.root / control_job
    selected_native = main / "ckpt" / "step_50" / "global_step_50"
    _serveable_export(selected_native)
    final_native = main / "ckpt" / "step_100" / "global_step_100"
    final_native.mkdir(parents=True)
    (final_native / "data.pt").write_bytes(b"resume")

    report = {
        "status": "completed",
        "before": {"pass_at_1": 0.5},
        "after": {"pass_at_1": 0.7},
        "selected_checkpoint_step": 50,
        "selection_rule": "maximum pass@1",
    }
    report_path = main / "artifacts" / "heldout" / "v0.12.7-report.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps(report), encoding="utf-8")
    for path, content in (
        (main / "artifacts" / "curve.png", b"m3-curve"),
        (control / "artifacts" / "curve.png", b"m4-curve"),
        (main / "artifacts" / "final" / "model.txt", b"m3-final"),
        (control / "artifacts" / "final" / "model.txt", b"m4-final"),
        (main / "evidence" / "heldout-after-v0127" / "report.json", b"heldout-proof"),
        (control / "evidence" / "control.txt", b"control-proof"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    _runtime_evidence(main / "evidence" / "runtime-environment.txt")
    _runtime_evidence(control / "evidence" / "runtime-environment.txt")
    return main_job, control_job


def test_m6_archive_records_selected_final_curves_and_every_preexisting_evidence(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "runs")
    main_job, control_job = _write_completed_pair(storage)

    evidence = create_archive(
        storage,
        main_job,
        control_job,
        report_artifact="heldout/v0.12.7-report.json",
        archive_artifact="m6/v0.12.7-archive-manifest.json",
    )

    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["heldout"]["selected_checkpoint_step"] == 50
    assert payload["selected_checkpoint"]["path"].endswith("step_50/global_step_50/actor/serveable_huggingface")
    assert payload["final_training_checkpoint"]["step"] == 100
    assert payload["curves"]["m3"]["sha256"] == sha256_file(
        storage.root / main_job / "artifacts" / "curve.png"
    )
    assert {entry["path"] for entry in payload["evidence_sha256"]["m3"]} >= {
        f"{main_job}/evidence/heldout-after-v0127/report.json",
        f"{main_job}/evidence/runtime-environment.txt",
    }
    artifact = storage.root / main_job / "artifacts" / "m6" / "v0.12.7-archive-manifest.json"
    assert artifact.read_bytes() == evidence.read_bytes()


def test_m6_archive_rejects_missing_runtime_pip_or_driver_evidence(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "runs")
    main_job, control_job = _write_completed_pair(storage)
    (storage.root / control_job / "evidence" / "runtime-environment.txt").write_text(
        "[pip_freeze]\nverl==0.8.0\n", encoding="utf-8"
    )

    with pytest.raises(M6ArchiveError, match="lacks pip/driver sections"):
        create_archive(
            storage,
            main_job,
            control_job,
            report_artifact="heldout/v0.12.7-report.json",
            archive_artifact="m6/v0.12.7-archive-manifest.json",
        )
