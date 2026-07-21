from pathlib import Path


README = Path(__file__).resolve().parents[1] / "README.md"


def test_gpt56_roles_precede_the_codex_work_log() -> None:
    text = README.read_text(encoding="utf-8")

    gpt_section = text.index("## How we used GPT-5.6")
    codex_section = text.index("## How we worked with Codex")

    assert gpt_section < codex_section
    section = text[gpt_section:codex_section]
    for required in (
        "Sol Ultra",
        "gpt-5.6-luna",
        "AgentDecision",
        "C-code Verifier",
        "review, tests, and the sandboxed validation boundary",
    ):
        assert required in section

    assert "Soul Ultra" not in section
