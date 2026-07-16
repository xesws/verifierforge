from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.eval_runner import EvaluationMetrics
from scripts import gate_a


SCHEMA = """
CREATE TABLE people (name TEXT NOT NULL);
INSERT INTO people VALUES ('Ada');
"""


class SequenceClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.models: list[str | None] = []

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        del messages, temperature
        self.models.append(model)
        return next(self.responses)


def _record(index: int) -> dict[str, object]:
    return {
        "id": f"case-{index}",
        "prompt": f"Return Ada's name ({index}).",
        "schema_sql": SCHEMA,
        "expected_results": [["Ada"]],
    }


def _write_candidates(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )


def _install_fake_client(monkeypatch, client: SequenceClient) -> None:
    settings = SimpleNamespace(
        model="configured-glm",
        base_url="https://key@router.test/v1?api_key=nope",
    )
    monkeypatch.setattr(gate_a, "_load_eval_client", lambda: (client, settings))


def test_gate_a_defaults_to_eight_bounded_workers() -> None:
    args = gate_a.build_parser().parse_args(
        ["candidates.jsonl", "--report", "gate-a.json"]
    )

    assert args.workers == 8


def test_gate_a_reports_raw_metrics_and_writes_secret_free_evidence(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    candidates = tmp_path / "candidates.jsonl"
    evidence = tmp_path / "gate-a.json"
    _write_candidates(candidates, [_record(1), _record(2)])
    client = SequenceClient(
        [
            "SELECT name FROM people",  # first group passes, then fails
            "SELECT name FROM people WHERE name = 'Nobody'",
            "SELECT name FROM people WHERE name = 'Nobody'",  # second does converse
            "SELECT name FROM people",
        ]
    )
    _install_fake_client(monkeypatch, client)

    exit_code = gate_a.main(
        [str(candidates), "--k", "2", "--workers", "1", "--report", str(evidence)]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "pass_at_1": 0.5,
        "mixed_fraction": 1.0,
        "pass_at_2": 1.0,
    }
    assert client.models == ["configured-glm"] * 4
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["status"] == "completed"
    assert payload["mode"] == "gate"
    assert payload["passed"] is True
    assert payload["candidate_count"] == 2
    assert payload["sample_count"] == 4
    assert payload["workers"] == 1
    assert payload["input_sha256"] == hashlib.sha256(candidates.read_bytes()).hexdigest()
    assert payload["base_url"] == "https://router.test/v1"
    assert payload["resolved_config"] == {
        "base_url": "https://router.test/v1",
        "model": "configured-glm",
    }
    assert "nope" not in evidence.read_text(encoding="utf-8")


def test_gate_a_evidence_adds_named_pass_at_8_without_removing_pass_at_k(
    tmp_path: Path,
) -> None:
    candidates = tmp_path / "candidates.jsonl"
    evidence = tmp_path / "gate-a.json"
    _write_candidates(candidates, [_record(1)])
    metrics = EvaluationMetrics(
        baseline_pass_at_1=0.25,
        pass_at_k=0.75,
        mixed_fraction=0.5,
        record_count=1,
        k=8,
    )

    gate_a.write_evidence(
        evidence,
        candidate_path=candidates,
        input_digest=hashlib.sha256(candidates.read_bytes()).hexdigest(),
        metrics=metrics,
        model="configured-glm",
        base_url="https://router.test/v1",
        workers=8,
    )

    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["k"] == 8
    assert payload["pass_at_k"] == 0.75
    assert payload["pass_at_8"] == 0.75


@pytest.mark.parametrize(
    ("configured_url", "expected"),
    [
        (
            "https://user:secret@router.test:8443/v1/chat?api_key=nope#fragment",
            "https://router.test:8443/v1/chat",
        ),
        (
            "https://user:secret@[2001:db8::1]:8443/v1?token=nope",
            "https://[2001:db8::1]:8443/v1",
        ),
        ("https://router.test:bad-port/v1?api_key=nope", "<configured>"),
    ],
)
def test_evidence_base_url_reveals_only_endpoint_identity(
    configured_url: str, expected: str
) -> None:
    value = gate_a._safe_base_url(configured_url)

    assert value == expected
    assert "secret" not in value
    assert "api_key" not in value
    assert "token" not in value
    assert "?" not in value
    assert "#" not in value


def test_gate_a_rejects_non_finite_json_cells_before_loading_a_client(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(
        json.dumps({**_record(1), "expected_results": [[float("nan")]]}) + "\n",
        encoding="utf-8",
    )

    def should_not_load_client() -> tuple[object, object]:
        raise AssertionError("invalid candidate data must fail before LLM setup")

    monkeypatch.setattr(gate_a, "_load_eval_client", should_not_load_client)

    evidence = tmp_path / "evidence.json"
    assert gate_a.main([str(candidates), "--report", str(evidence)]) == 2
    assert "finite SQL scalar JSON value" in capsys.readouterr().err
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["failure"]["category"] == "input"


def test_gate_a_load_eval_client_uses_only_eval_settings(monkeypatch) -> None:
    from app import gpt

    captured: dict[str, object] = {}
    settings = SimpleNamespace(
        api_key="vf-local-eval",
        model="Qwen2.5-1.5B-Instruct",
        base_url="http://127.0.0.1:8000/v1",
    )

    class FakeSettings:
        @classmethod
        def from_env(cls) -> SimpleNamespace:
            captured["settings_loader"] = "eval"
            return settings

    class FakeClient:
        def __init__(self, received_settings: object) -> None:
            self.settings = received_settings

    monkeypatch.setattr(gpt, "EvalSettings", FakeSettings)
    monkeypatch.setattr(gpt, "LLMClient", FakeClient)

    client, loaded_settings = gate_a._load_eval_client()

    assert captured == {"settings_loader": "eval"}
    assert client.settings is settings
    assert loaded_settings is settings


def test_gate_a_accepts_exact_human_threshold_boundaries(
    tmp_path: Path, monkeypatch
) -> None:
    candidates = tmp_path / "candidates.jsonl"
    _write_candidates(candidates, [_record(index) for index in range(10)])
    # pass@1 = 2/10 = 0.20; mixed = 3/10 = 0.30.  Both inclusive boundaries
    # must pass instead of being silently made stricter.
    client = SequenceClient(
        [
            "SELECT name FROM people",
            "SELECT name FROM people WHERE name = 'Nobody'",
            "SELECT name FROM people",
            "SELECT name FROM people WHERE name = 'Nobody'",
            "SELECT name FROM people WHERE name = 'Nobody'",
            "SELECT name FROM people",
        ]
        + ["SELECT name FROM people WHERE name = 'Nobody'"] * 14
    )
    _install_fake_client(monkeypatch, client)

    assert (
        gate_a.main(
            [
                "--input",
                str(candidates),
                "--k",
                "2",
                "--workers",
                "1",
                "--report",
                str(tmp_path / "evidence.json"),
            ]
        )
        == 0
    )


def test_gate_a_fails_closed_and_still_records_measured_evidence(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    candidates = tmp_path / "candidates.jsonl"
    evidence = tmp_path / "gate-a.json"
    _write_candidates(candidates, [_record(1), _record(2)])
    client = SequenceClient(["SELECT name FROM people"] * 4)
    _install_fake_client(monkeypatch, client)

    exit_code = gate_a.main(
        [str(candidates), "--k", "2", "--workers", "1", "--report", str(evidence)]
    )

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out) == {
        "pass_at_1": 1.0,
        "mixed_fraction": 0.0,
        "pass_at_2": 1.0,
    }
    assert json.loads(evidence.read_text(encoding="utf-8"))["passed"] is False


def test_gate_a_never_renders_provider_exception_text(tmp_path: Path, monkeypatch, capsys) -> None:
    candidates = tmp_path / "candidates.jsonl"
    _write_candidates(candidates, [_record(1)])

    class FailingClient:
        def complete(self, *args, **kwargs) -> str:
            del args, kwargs
            raise RuntimeError("Authorization: Bearer definitely-not-for-output")

    _install_fake_client(monkeypatch, FailingClient())

    assert (
        gate_a.main(
            [
                str(candidates),
                "--k",
                "1",
                "--workers",
                "1",
                "--report",
                str(tmp_path / "evidence.json"),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "definitely-not-for-output" not in captured.out
    assert "definitely-not-for-output" not in captured.err


def test_gate_a_persists_retry_and_provider_failure_evidence(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    candidates = tmp_path / "candidates.jsonl"
    evidence = tmp_path / "gate-a.json"
    _write_candidates(candidates, [_record(1)])
    secret = "sk-failure-secret"

    class ProviderFailure(RuntimeError):
        def __init__(self) -> None:
            super().__init__(f"Authorization: Bearer {secret}")
            self.status_code = 503
            self.body = {"detail": f"Authorization: Bearer {secret}"}

    class FailingClient:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, *args, **kwargs) -> str:
            del args, kwargs
            self.calls += 1
            raise ProviderFailure()

    client = FailingClient()
    _install_fake_client(monkeypatch, client)

    assert (
        gate_a.main(
            [str(candidates), "--k", "1", "--workers", "1", "--report", str(evidence)]
        )
        == 2
    )

    captured = capsys.readouterr()
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    failure = payload["failure"]
    sample_failure = failure["failures"][0]
    assert client.calls == 2
    assert payload["schema_version"] == 2
    assert payload["status"] == "failed"
    assert payload["resolved_config"] == {
        "base_url": "https://router.test/v1",
        "model": "configured-glm",
    }
    assert failure["category"] == "completion"
    assert failure["circuit_open"] is False
    assert failure["terminal_failure_count"] == 1
    assert sample_failure["request_ordinal"] == 1
    assert sample_failure["record_index"] == 1
    assert sample_failure["sample_index"] == 1
    assert sample_failure["attempt_count"] == 2
    assert [item["status_code"] for item in sample_failure["attempts"]] == [503, 503]
    assert "ProviderFailure" in [item["type"] for item in sample_failure["exception_chain"]]
    assert secret not in evidence.read_text(encoding="utf-8")
    assert secret not in captured.out
    assert secret not in captured.err


def test_gate_a_persists_config_failure_evidence_without_llm_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    candidates = tmp_path / "candidates.jsonl"
    evidence = tmp_path / "gate-a.json"
    _write_candidates(candidates, [_record(1)])
    monkeypatch.delenv("VF_EVAL_BASE_URL", raising=False)
    monkeypatch.delenv("VF_EVAL_MODEL", raising=False)
    monkeypatch.setenv("VF_LLM_API_KEY", "must-not-be-used")

    assert gate_a.main([str(candidates), "--report", str(evidence)]) == 2

    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["failure"]["category"] == "configuration"
    assert payload["resolved_config"] == {"base_url": None, "model": None}
    assert "must-not-be-used" not in evidence.read_text(encoding="utf-8")


def test_reference_mode_records_threshold_status_but_does_not_reject(
    tmp_path: Path, monkeypatch
) -> None:
    candidates = tmp_path / "candidates.jsonl"
    evidence = tmp_path / "full-gate-a.json"
    _write_candidates(candidates, [_record(1)])
    _install_fake_client(monkeypatch, SequenceClient(["SELECT name FROM people"] * 2))

    assert (
        gate_a.main(
            [
                str(candidates),
                "--k",
                "2",
                "--workers",
                "1",
                "--report",
                str(evidence),
                "--reference",
            ]
        )
        == 0
    )

    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["mode"] == "reference"
    assert payload["passed"] is False
