from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "blog" / "technical-deep-dive.md"


def test_technical_deep_dive_has_the_frozen_pdf_shape() -> None:
    markdown = SOURCE.read_text(encoding="utf-8")
    chapters = re.findall(r"^## (.+)$", markdown, flags=re.MULTILINE)
    figures = re.findall(r"!\[[^]]*\]\((\./figures/[^)]+\.svg)\)", markdown)

    assert len(chapters) == 12
    assert len(figures) == len(set(figures)) == 6
    assert chapters[0] == "The six-step loop"
    assert chapters[-1] == "References"


def test_print_mode_keeps_one_source_and_forces_complete_rendering() -> None:
    page = (ROOT / "frontend" / "src" / "pages" / "TechPage.tsx").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "src" / "styles" / "index.css").read_text(encoding="utf-8")
    exporter = (ROOT / "scripts" / "export_technical_deep_dive.py").read_text(encoding="utf-8")

    assert "technical-deep-dive.md?raw" in page
    assert "get('print') === '1'" in page
    assert "eagerImages={printMode}" in page
    assert "data-chapter-count={content.chapters.length}" in page
    assert "@media print" in css
    assert ".tech-chapter { order: 3; overflow: visible; break-before: page;" in css
    assert "EXPECTED_CHAPTERS = 12" in exporter
    assert "EXPECTED_FIGURES = 6" in exporter
    assert 'ROOT / "docs" / "submission" / "verifierforge-technical-deep-dive.pdf"' in exporter
    assert 'ROOT / "output"' not in exporter
    assert '"Page.printToPDF"' in exporter
    assert "_wait_for_print_ready" in exporter
