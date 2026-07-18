from __future__ import annotations

from scripts import freeze_nl2sql


def test_legacy_freezer_delegates_every_argument(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def delegated(argv):
        captured["argv"] = argv
        return 17

    monkeypatch.setattr(freeze_nl2sql.freeze_three_piece, "main", delegated)
    arguments = ["--training-pool", "training.jsonl", "--heldout", "heldout.jsonl"]

    assert freeze_nl2sql.main(arguments) == 17
    assert captured["argv"] is arguments
    assert "deprecated" in capsys.readouterr().err


def test_legacy_freezer_contains_no_obsolete_projection_rule() -> None:
    assert not hasattr(freeze_nl2sql, "SELECTION_RULE")
    assert not hasattr(freeze_nl2sql, "build_manifest")
