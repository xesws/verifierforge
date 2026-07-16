from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

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
    monkeypatch.setattr(gate_a, "_load_client", lambda: (client, settings))


def test_gate_a_defaults_to_eight_bounded_workers() -> None:
    args = gate_a.build_parser().parse_args(["candidates.jsonl"])

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
    assert payload["passed"] is True
    assert payload["candidate_count"] == 2
    assert payload["sample_count"] == 4
    assert payload["workers"] == 1
    assert payload["input_sha256"] == hashlib.sha256(candidates.read_bytes()).hexdigest()
    assert payload["base_url"] == "https://router.test"
    assert "nope" not in evidence.read_text(encoding="utf-8")


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

    assert gate_a.main(["--input", str(candidates), "--k", "2", "--workers", "1"]) == 0


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

    assert gate_a.main([str(candidates), "--k", "1", "--workers", "1"]) == 2
    captured = capsys.readouterr()
    assert "definitely-not-for-output" not in captured.out
    assert "definitely-not-for-output" not in captured.err
