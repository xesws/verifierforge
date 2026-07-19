from __future__ import annotations

import json
from pathlib import Path

import pytest

from trainer import serving_smoke
from trainer.export_compat import ExportCompatibilityError


class _FakeProcess:
    def __init__(self) -> None:
        self.stopped = False

    def poll(self) -> int | None:
        return 0 if self.stopped else None


def test_loopback_smoke_requires_models_and_completion_and_preserves_exchange(tmp_path: Path) -> None:
    export = tmp_path / "global_step_50" / "actor" / "serveable_huggingface"
    export.mkdir(parents=True)
    evidence = tmp_path / "evidence" / "step_50.json"
    process = _FakeProcess()
    model_names: list[str] = []
    stopped: list[bool] = []

    def launcher(_export: Path, _port: int, model_name: str, _evidence: Path) -> serving_smoke._ServerHandle:
        model_names.append(model_name)
        log = tmp_path / "vllm.log"
        log.write_text("server ready\n", encoding="utf-8")
        return serving_smoke._ServerHandle(
            process=process,
            command=["vllm", "serve"],
            log_path=log,
            stop=lambda: (setattr(process, "stopped", True), stopped.append(True)),
        )

    def request(url: str, payload: dict[str, object] | None) -> tuple[int, dict[str, object]]:
        if url.endswith("/v1/models"):
            return 200, {"data": [{"id": model_names[0]}]}
        assert payload == {
            "model": model_names[0],
            "prompt": "Return exactly: SELECT 1;",
            "max_tokens": 8,
            "temperature": 0.0,
        }
        return 200, {"choices": [{"text": " SELECT 1;"}]}

    result = serving_smoke.smoke_serveable_export(
        export,
        evidence_path=evidence,
        launcher=launcher,
        request_json=request,
        port_selector=lambda: 19001,
    )

    assert result.port == 19001
    assert stopped == [True]
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["models"]["data"][0]["id"] == result.model_name
    assert payload["completion"]["choices"][0]["text"] == " SELECT 1;"


def test_loopback_smoke_writes_failure_evidence_and_stops_server(tmp_path: Path) -> None:
    export = tmp_path / "global_step_50" / "actor" / "serveable_huggingface"
    export.mkdir(parents=True)
    evidence = tmp_path / "evidence" / "step_50.json"
    process = _FakeProcess()
    stopped: list[bool] = []

    def launcher(_export: Path, _port: int, model_name: str, _evidence: Path) -> serving_smoke._ServerHandle:
        log = tmp_path / "vllm.log"
        log.write_text("completion error\n", encoding="utf-8")
        return serving_smoke._ServerHandle(
            process=process,
            command=["vllm", "serve", model_name],
            log_path=log,
            stop=lambda: (setattr(process, "stopped", True), stopped.append(True)),
        )

    def request(url: str, _payload: dict[str, object] | None) -> tuple[int, dict[str, object]]:
        if url.endswith("/v1/models"):
            return 200, {"data": [{"id": "vf-serving-smoke-global_step_50"}]}
        return 500, {"error": "synthetic completion failure"}

    with pytest.raises(serving_smoke.ServingSmokeError, match="completion endpoint"):
        serving_smoke.smoke_serveable_export(
            export,
            evidence_path=evidence,
            launcher=launcher,
            request_json=request,
            port_selector=lambda: 19002,
        )

    assert stopped == [True]
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["completion_status"] == 500
    assert payload["error"]["type"] == "ServingSmokeError"


def test_conversion_failure_is_evidence_before_any_vllm_launch(monkeypatch, tmp_path: Path) -> None:
    native = tmp_path / "global_step_50"
    evidence = tmp_path / "evidence" / "step_50.json"
    launches: list[bool] = []

    def fail_conversion(*_args: object, **_kwargs: object) -> object:
        raise ExportCompatibilityError("incomplete LoRA pair")

    monkeypatch.setattr(serving_smoke, "convert_prefixed_full_export", fail_conversion)

    with pytest.raises(serving_smoke.ServingSmokeError, match="conversion failed"):
        serving_smoke.validate_checkpoint_for_serving(
            native,
            lora_rank=16,
            lora_alpha=32,
            evidence_path=evidence,
            launcher=lambda *_args: launches.append(True),  # type: ignore[arg-type]
        )

    assert launches == []
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["phase"] == "conversion"
    assert payload["error"] == {"type": "ExportCompatibilityError", "message": "incomplete LoRA pair"}


def test_vllm_command_reserves_memory_above_the_resident_trainer() -> None:
    command = serving_smoke._vllm_command(
        Path("/venv/bin/vllm"),
        Path("/checkpoint/serveable_huggingface"),
        19003,
        "vf-smoke",
    )

    option = command.index("--gpu-memory-utilization")
    assert command[option + 1] == "0.70"
    assert "--enforce-eager" in command
    assert command[command.index("--max-model-len") + 1] == "64"
    assert command[command.index("--max-num-seqs") + 1] == "1"


def test_vllm_failure_tail_keeps_a_root_cause_beyond_the_old_limit(tmp_path: Path) -> None:
    log = tmp_path / "vllm.log"
    marker = "ValueError: No available memory for the cache blocks."
    log.write_text("x" * 10_000 + marker + "\n" + "y" * 3_000, encoding="utf-8")

    assert marker in serving_smoke._tail(log)
