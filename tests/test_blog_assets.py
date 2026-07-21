from __future__ import annotations

import json
from pathlib import Path
import xml.etree.ElementTree as ET

from scripts import build_blog_assets


SVG = "{http://www.w3.org/2000/svg}"


def test_committed_blog_assets_are_current() -> None:
    assert build_blog_assets.check_outputs() == []


def test_every_blog_figure_has_accessible_copy_and_source_metadata() -> None:
    figures = sorted(build_blog_assets.FIGURE_DIR.glob("*.svg"))
    assert [path.name for path in figures] == [
        "01-spurious-control.svg",
        "02-agent-system.svg",
        "03-grpo-loop.svg",
        "04-verifier-pipeline.svg",
        "05-heldout-selection.svg",
        "06-system-loop.svg",
    ]
    for path in figures:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
        assert root.find(f"{SVG}title").text
        assert root.find(f"{SVG}desc").text
        metadata = json.loads(root.find(f"{SVG}metadata").text or "{}")
        assert metadata["sources"]
        for source in metadata["sources"]:
            source_path = build_blog_assets.ROOT / source["path"]
            assert source_path.is_file()
            assert build_blog_assets._sha(Path(source["path"])) == source["sha256"]


def test_data_figures_bind_the_published_results() -> None:
    control_root = ET.fromstring(
        (build_blog_assets.FIGURE_DIR / "01-spurious-control.svg").read_text()
    )
    control = json.loads(control_root.find(f"{SVG}metadata").text or "{}")
    assert control["facts"] == {
        "control_final": 0.4,
        "control_steps": 200,
        "main_final": 0.8,
        "main_steps": 400,
    }

    heldout_root = ET.fromstring(
        (build_blog_assets.FIGURE_DIR / "05-heldout-selection.svg").read_text()
    )
    heldout = json.loads(heldout_root.find(f"{SVG}metadata").text or "{}")
    assert heldout["facts"]["selected_step"] == 350
    assert heldout["facts"]["selected_pass_at_1"] == 0.7833333333333333


def test_favicon_is_the_canonical_mark() -> None:
    mark = build_blog_assets.BRAND_DIR / "verifierforge-mark.svg"
    favicon = build_blog_assets.ROOT / "frontend" / "public" / "favicon.svg"
    assert mark.read_bytes() == favicon.read_bytes()
