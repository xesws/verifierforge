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


def test_agent_gate_c_encloses_the_strict_exit_and_score() -> None:
    root = ET.fromstring(
        (build_blog_assets.FIGURE_DIR / "02-agent-system.svg").read_text()
    )
    rects = root.findall(f"{SVG}rect")

    evaluator = next(
        node for node in rects if node.attrib.get("x") == "72" and node.attrib.get("y") == "154"
    )
    strict_exit = next(
        node for node in rects if node.attrib.get("x") == "350" and node.attrib.get("y") == "530"
    )
    gate_score = next(
        node for node in rects if node.attrib.get("x") == "100" and node.attrib.get("y") == "550"
    )

    evaluator_right = float(evaluator.attrib["x"]) + float(evaluator.attrib["width"])
    evaluator_bottom = float(evaluator.attrib["y"]) + float(evaluator.attrib["height"])
    for node in (strict_exit, gate_score):
        assert float(node.attrib["x"]) + float(node.attrib["width"]) <= evaluator_right
        assert float(node.attrib["y"]) + float(node.attrib["height"]) <= evaluator_bottom

    text = " ".join("".join(node.itertext()) for node in root.findall(f"{SVG}text"))
    assert "GATE C EVALUATOR" in text
    assert "HUMAN APPROVAL WALL" in text


def test_favicon_is_the_canonical_mark() -> None:
    mark = build_blog_assets.BRAND_DIR / "verifierforge-mark.svg"
    favicon = build_blog_assets.ROOT / "frontend" / "public" / "favicon.svg"
    assert mark.read_bytes() == favicon.read_bytes()


def test_brand_mark_matches_the_product_vf_visual_language() -> None:
    root = ET.fromstring(
        (build_blog_assets.BRAND_DIR / "verifierforge-mark.svg").read_text()
    )
    gradient = root.find(f".//{SVG}linearGradient[@id='vf-product-gradient']")
    assert gradient is not None
    assert [stop.attrib["stop-color"] for stop in gradient.findall(f"{SVG}stop")] == [
        "#1488f4",
        "#0872dd",
        "#00a67e",
    ]
    assert [node.text for node in root.findall(f"{SVG}text")] == ["V", "F"]


def test_frontend_bundle_is_generated_from_the_canonical_sources() -> None:
    web = build_blog_assets.WEB_CONTENT_DIR
    assert (web / "technical-deep-dive.md").read_bytes() == (
        build_blog_assets.ARTICLE_PATH.read_bytes()
    )
    assert (web / "verifierforge-wordmark.svg").read_bytes() == (
        build_blog_assets.BRAND_DIR / "verifierforge-wordmark.svg"
    ).read_bytes()
    for figure in build_blog_assets.FIGURE_DIR.glob("*.svg"):
        assert (web / "figures" / figure.name).read_bytes() == figure.read_bytes()
