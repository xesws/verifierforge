"""Build deterministic SVG assets for the v0.36.0 technical deep dive."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import html
import json
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
FIGURE_DIR = ROOT / "docs" / "blog" / "figures"
BRAND_DIR = ROOT / "assets" / "brand"
WEB_CONTENT_DIR = (
    ROOT / "frontend" / "src" / "generated" / "technical-deep-dive"
)
ARTICLE_PATH = ROOT / "docs" / "blog" / "technical-deep-dive.md"

MAIN_METRICS = Path("data/demo-artifacts/jobs/d4-m3-1p5b-r1-v0125/metrics.jsonl")
CONTROL_METRICS = Path("data/demo-artifacts/jobs/d4-m4-0p5b-random-v0126/metrics.jsonl")
HELDOUT_REPORT = Path(
    "data/demo-artifacts/jobs/d4-m3-1p5b-r1-v0125/heldout-report.json"
)
GATE_C_REPORT = Path("runs/forge-agent/gate-c-v0223-round2.json")
SERVING_EVIDENCE = Path("docs/evidence/serving/v0.34.0-sv5-live.json")
CAPACITY_EVIDENCE = Path("docs/evidence/provisioner/v0.32.0-capacity-live-pass.json")

GRAPHITE = "#17212b"
MUTED = "#62717d"
SOFT = "#82909a"
BLUE = "#087cf0"
GREEN = "#00a67e"
AMBER = "#a96900"
CORAL = "#dd6b5b"
BG = "#edf5f7"
CARD = "#ffffff"
LINE = "#d6e2e6"


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _sha(path: Path) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (ROOT / path).read_text().splitlines() if line]


def _metadata(paths: Iterable[Path], **facts: object) -> str:
    payload = {
        "sources": [{"path": str(path), "sha256": _sha(path)} for path in paths],
        "facts": facts,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


@dataclass
class SVG:
    title: str
    description: str
    width: int = 1200
    height: int = 680
    metadata: str = "{}"

    def __post_init__(self) -> None:
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.width} {self.height}" role="img" aria-labelledby="title desc">',
            f"<title id=\"title\">{_escape(self.title)}</title>",
            f"<desc id=\"desc\">{_escape(self.description)}</desc>",
            f"<metadata>{_escape(self.metadata)}</metadata>",
            "<defs>",
            '<filter id="shadow" x="-15%" y="-15%" width="130%" height="140%"><feDropShadow dx="0" dy="10" stdDeviation="14" flood-color="#304d5b" flood-opacity=".10"/></filter>',
            '<linearGradient id="accent" x1="0" x2="1"><stop stop-color="#087cf0"/><stop offset="1" stop-color="#00a67e"/></linearGradient>',
            '<style>text{font-family:Manrope,Inter,Arial,sans-serif;fill:#17212b}.mono{font-family:"IBM Plex Mono",Menlo,monospace}.label{font-size:13px;letter-spacing:.09em;text-transform:uppercase;font-weight:700}.small{font-size:13px}.tiny{font-size:11px}.body{font-size:16px}.heading{font-size:26px;font-weight:750;letter-spacing:-.03em}.hero{font-size:34px;font-weight:780;letter-spacing:-.04em}.muted{fill:#62717d}.soft{fill:#82909a}.blue{fill:#087cf0}.green{fill:#00a67e}.amber{fill:#a96900}.coral{fill:#dd6b5b}.white{fill:#fff}.stroke{stroke:#d6e2e6;stroke-width:1.5}.arrow{stroke:#82909a;stroke-width:2;fill:none;marker-end:url(#arrow)}</style>',
            '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#82909a"/></marker>',
            "</defs>",
            f'<rect width="{self.width}" height="{self.height}" rx="28" fill="{BG}"/>',
        ]

    def add(self, value: str) -> None:
        self.parts.append(value)

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        fill: str = CARD,
        radius: float = 18,
        stroke: str | None = LINE,
        shadow: bool = False,
        opacity: float | None = None,
    ) -> None:
        attrs = [f'x="{x}"', f'y="{y}"', f'width="{w}"', f'height="{h}"', f'rx="{radius}"', f'fill="{fill}"']
        if stroke:
            attrs.append(f'stroke="{stroke}"')
        if shadow:
            attrs.append('filter="url(#shadow)"')
        if opacity is not None:
            attrs.append(f'opacity="{opacity}"')
        self.add(f"<rect {' '.join(attrs)}/>")

    def text(self, x: float, y: float, value: object, *, cls: str = "body", anchor: str = "start", fill: str | None = None) -> None:
        color = f' fill="{fill}"' if fill else ""
        self.add(f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}"{color}>{_escape(value)}</text>')

    def multiline(self, x: float, y: float, lines: Sequence[str], *, cls: str = "small", gap: float = 20, anchor: str = "start") -> None:
        spans = "".join(
            f'<tspan x="{x}" dy="{0 if index == 0 else gap}">{_escape(line)}</tspan>'
            for index, line in enumerate(lines)
        )
        self.add(f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}">{spans}</text>')

    def arrow(self, x1: float, y1: float, x2: float, y2: float, *, color: str = SOFT, dashed: bool = False) -> None:
        dash = ' stroke-dasharray="7 7"' if dashed else ""
        self.add(f'<path d="M{x1} {y1} L{x2} {y2}" stroke="{color}" stroke-width="2" fill="none" marker-end="url(#arrow)"{dash}/>' )

    def pill(self, x: float, y: float, label: str, *, fill: str, text_fill: str = CARD, width: float | None = None) -> None:
        width = width or max(80, len(label) * 7.2 + 24)
        self.rect(x, y, width, 28, fill=fill, radius=14, stroke=None)
        self.text(x + width / 2, y + 19, label, cls="tiny mono", anchor="middle", fill=text_fill)

    def finish(self) -> str:
        return "\n".join([*self.parts, "</svg>", ""])


def _header(svg: SVG, eyebrow: str, title: str, subtitle: str) -> None:
    svg.text(48, 48, eyebrow, cls="label blue")
    svg.text(48, 84, title, cls="heading")
    svg.text(48, 111, subtitle, cls="small muted")


def _polyline(points: Sequence[tuple[float, float]], color: str, width: float = 3, opacity: float = 1) -> str:
    encoded = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    return f'<polyline points="{encoded}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round" opacity="{opacity}"/>'


def build_spurious_control() -> str:
    main = _jsonl(MAIN_METRICS)
    control = _jsonl(CONTROL_METRICS)
    svg = SVG(
        "Verifier reward versus deterministic random-reward control",
        "Actual 400-step main and 200-step control monitoring curves, with the experimental differences disclosed.",
        metadata=_metadata(
            [MAIN_METRICS, CONTROL_METRICS],
            main_steps=len(main),
            control_steps=len(control),
            main_final=main[-1]["pass_at_1"],
            control_final=control[-1]["pass_at_1"],
        ),
    )
    _header(svg, "FIGURE 1 · FALSIFICATION REFERENCE", "Did the verifier carry the signal?", "Same frozen 50-row pool and GRPO skeleton; model size, duration, and reward differ.")
    svg.rect(44, 140, 410, 490, shadow=True)
    svg.text(70, 178, "Design ledger", cls="heading")
    svg.pill(70, 198, "SHARED", fill=GREEN)
    svg.multiline(70, 248, ["Frozen 50-row training pool", "k = 8 rollouts · batch = 4", "LoRA 16/32 · LR 1e-6 · KL 0.001"], cls="small")
    svg.rect(70, 322, 156, 198, fill="#eaf4ff", stroke="#c8e1f8")
    svg.text(90, 354, "M3", cls="label blue")
    svg.text(90, 386, "1.5B", cls="hero")
    svg.multiline(90, 421, ["400 steps", "SQL verifier", "final monitor 0.80"], cls="small")
    svg.rect(242, 322, 168, 198, fill="#fff4e3", stroke="#f0d7aa")
    svg.text(262, 354, "M4", cls="label amber")
    svg.text(262, 386, "0.5B", cls="hero")
    svg.multiline(262, 421, ["200 steps", "Bernoulli(0.5)", "final monitor 0.40"], cls="small")
    svg.rect(70, 540, 340, 62, fill="#fff0ee", stroke="#f2c9c3")
    svg.multiline(86, 565, ["Falsification reference, not a", "single-variable causal estimate."], cls="small coral", gap=18)

    x0, y0, cw, ch = 520, 180, 620, 385
    svg.rect(488, 140, 668, 490, shadow=True)
    svg.text(520, 174, "Training-pool monitoring pass@1", cls="body")
    for tick in [0.4, 0.5, 0.6, 0.7, 0.8]:
        y = y0 + ch - (tick - 0.3) / 0.6 * ch
        svg.add(f'<line x1="{x0}" y1="{y:.2f}" x2="{x0 + cw}" y2="{y:.2f}" stroke="{LINE}" stroke-width="1"/>')
        svg.text(x0 - 12, y + 4, f"{tick:.1f}", cls="tiny mono muted", anchor="end")
    for tick in [0, 100, 200, 300, 400]:
        x = x0 + tick / 400 * cw
        svg.text(x, y0 + ch + 26, tick, cls="tiny mono muted", anchor="middle")
    def point(row: dict[str, object]) -> tuple[float, float]:
        return (
            x0 + float(row["step"]) / 400 * cw,
            y0 + ch - (float(row["pass_at_1"]) - 0.3) / 0.6 * ch,
        )
    svg.add(_polyline([point(row) for row in main], BLUE, width=3.5, opacity=.86))
    svg.add(_polyline([point(row) for row in control], AMBER, width=3.5, opacity=.9))
    svg.pill(826, 158, "M3 · verifier", fill=BLUE, width=132)
    svg.pill(968, 158, "M4 · random", fill=AMBER, width=132)
    svg.text(830, 604, "If formatting alone caused the gain, both curves should rise.", cls="small", anchor="middle")
    return svg.finish()


def build_agent_system() -> str:
    report = json.loads((ROOT / GATE_C_REPORT).read_text())
    metrics = report["metrics"]
    svg = SVG(
        "Forge Agent decision system and Gate C",
        "GPT-5.6 uses four read-only tools, exits through a strict schema, and cannot reach the Provisioner without human approval.",
        metadata=_metadata([GATE_C_REPORT], scenario_count=report["scenario_count"], metrics=metrics),
    )
    _header(svg, "FIGURE 2 · AGENT SYSTEM", "A decision engine with no spending handle", "Responses tool-calling is audited; execution begins only beyond a human approval wall.")
    svg.rect(44, 140, 1112, 490, shadow=True)
    svg.rect(80, 190, 235, 330, fill="#eef6ff", stroke="#c8e1f8")
    svg.text(108, 224, "GPT-5.6 LUNA", cls="label blue")
    svg.text(108, 258, "ReAct loop", cls="heading")
    svg.multiline(108, 300, ["Responses API", "bounded turns/tokens/time", "structured tool calls", "audited AgentTrace"], cls="small", gap=24)
    svg.pill(108, 415, "READ ONLY", fill=GREEN, width=116)
    svg.multiline(108, 462, ["No Provisioner import", "No provider credential", "No spending handle"], cls="tiny muted", gap=19)
    tools = ["analyze_traffic", "inspect_samples", "estimate_economics", "check_verifiability"]
    for index, tool in enumerate(tools):
        y = 176 + index * 83
        svg.rect(378, y, 230, 58, fill=CARD, stroke=LINE)
        svg.text(493, y + 35, tool, cls="small mono", anchor="middle")
        svg.arrow(315, y + 29, 378, y + 29, color=BLUE)
        svg.arrow(608, y + 29, 665, y + 29, color=GREEN)
    svg.rect(665, 190, 180, 310, fill="#f0faf7", stroke="#bfe6da")
    svg.text(755, 225, "DATA LAYER", cls="label green", anchor="middle")
    svg.multiline(755, 271, ["Supabase facts", "approved samples", "cost assumptions", "verifier evidence"], cls="small", gap=32, anchor="middle")
    svg.rect(350, 535, 510, 62, fill="#17212b", stroke=None)
    svg.text(605, 561, "submit_decision · strict AgentDecision schema", cls="small mono white", anchor="middle")
    svg.text(605, 583, "forge | skip | need_more_data", cls="tiny mono", anchor="middle", fill="#9ddbc9")
    svg.add(f'<line x1="890" y1="170" x2="890" y2="590" stroke="{CORAL}" stroke-width="5" stroke-dasharray="8 8"/>')
    svg.text(904, 204, "HUMAN APPROVAL WALL", cls="label coral")
    svg.rect(930, 250, 180, 104, fill="#fff4e3", stroke="#f0d7aa")
    svg.text(1020, 284, "Approve", cls="heading", anchor="middle")
    svg.text(1020, 315, "writes intent only", cls="small muted", anchor="middle")
    svg.rect(930, 382, 180, 104, fill="#eaf4ff", stroke="#c8e1f8")
    svg.text(1020, 417, "Start Forge", cls="heading", anchor="middle")
    svg.text(1020, 448, "Provisioner begins", cls="small muted", anchor="middle")
    svg.rect(72, 154, 806, 394, fill="none", stroke="#98b9c8", radius=24)
    svg.text(680, 166, "EVALUATOR · 12 scenarios · schema / chain / decision / config", cls="tiny mono muted", anchor="middle")
    svg.pill(944, 535, "GATE C  1 / 1 / 0 / 1", fill=GREEN, width=176)
    return svg.finish()


def build_grpo_loop() -> str:
    config = Path("trainer/verl_configs/grpo_v1_1p5b_h100_main.yaml")
    svg = SVG(
        "Verifier-backed GRPO training loop",
        "A prompt produces eight rollouts, the verifier scores them, group-relative advantages are computed, and a KL-constrained policy update follows.",
        metadata=_metadata([config], rollout_n=8, steps=400, kl_loss_coef=0.001, lora_rank=16),
    )
    _header(svg, "FIGURE 3 · GRPO", "Eight attempts become one relative learning signal", "Verifier scores replace a learned critic; the group supplies its own baseline.")
    centers = [(130, 286), (350, 286), (585, 286), (820, 286), (1060, 286)]
    labels = [
        ("PROMPT", ["Frozen NL→SQL row"]),
        ("k = 8", ["vLLM rollouts", "temperature 1.0"]),
        ("VERIFIER", ["0.2 / 0.5 / 1.0", "per completion"]),
        ("RELATIVE A", ["subtract group mean", "normalize by spread"]),
        ("UPDATE", ["clipped objective", "+ KL to reference"]),
    ]
    for index, ((cx, cy), (title, body)) in enumerate(zip(centers, labels, strict=True)):
        color = [BLUE, BLUE, GREEN, AMBER, GRAPHITE][index]
        svg.add(f'<circle cx="{cx}" cy="{cy}" r="82" fill="{CARD}" stroke="{color}" stroke-width="3" filter="url(#shadow)"/>')
        svg.text(cx, cy - 12, title, cls="label", anchor="middle", fill=color)
        svg.multiline(cx, cy + 20, body, cls="tiny muted", gap=17, anchor="middle")
        if index < len(centers) - 1:
            svg.arrow(cx + 84, cy, centers[index + 1][0] - 84, cy)
    svg.arrow(1060, 374, 1060, 472, color=BLUE)
    svg.arrow(1060, 472, 130, 472, color=BLUE)
    svg.arrow(130, 472, 130, 374, color=BLUE)
    svg.text(595, 496, "repeat for 400 steps · checkpoint every 50 · entropy brake armed", cls="small mono blue", anchor="middle")
    svg.rect(145, 535, 910, 78, fill="#17212b", stroke=None)
    svg.text(600, 565, "Aᵢ = (rᵢ − mean(r₁…r₈)) / (std(r₁…r₈) + ε)", cls="body mono white", anchor="middle")
    svg.text(600, 591, "maximize clipped policy gain − β · KL(πθ || πref)", cls="small mono", anchor="middle", fill="#9ddbc9")
    return svg.finish()


def build_verifier_pipeline() -> str:
    verifier = Path("core/rewards/nl2sql.py")
    svg = SVG(
        "NL2SQL verifier tier pipeline",
        "SQL is extracted, parsed, executed read-only against frozen SQLite, and its result multiset is compared with expected rows.",
        metadata=_metadata([verifier], verifier_version=2, tiers=[0.2, 0.5, 1.0], length_penalty=0.05),
    )
    _header(svg, "FIGURE 4 · PROGRAMMATIC VERIFIER", "A score you can debug one tier at a time", "The highest achieved tier wins; this is not a weighted sum.")
    svg.rect(44, 140, 1112, 470, shadow=True)
    stages = [
        (80, "1", "EXTRACT", ["Markdown SQL fence", "retain raw completion"], BLUE),
        (335, "2", "PARSE + GUARD", ["one SELECT / WITH", "tier = 0.2"], BLUE),
        (590, "3", "EXECUTE", ["frozen SQLite schema", "read-only · tier = 0.5"], GREEN),
        (845, "4", "COMPARE", ["Counter(result rows)", "exact multiset · tier = 1.0"], GREEN),
    ]
    for index, (x, number, title, body, color) in enumerate(stages):
        svg.rect(x, 205, 210, 220, fill="#f8fbfc", stroke=LINE)
        svg.add(f'<circle cx="{x + 34}" cy="239" r="18" fill="{color}"/>')
        svg.text(x + 34, 244, number, cls="small mono white", anchor="middle")
        svg.text(x + 24, 286, title, cls="label", fill=color)
        svg.multiline(x + 24, 330, body, cls="small", gap=25)
        if index < len(stages) - 1:
            svg.arrow(x + 210, 315, stages[index + 1][0] - 10, 315)
    svg.rect(130, 468, 940, 94, fill="#eef6ff", stroke="#c8e1f8")
    svg.text(600, 501, "final score = highest achieved tier", cls="heading", anchor="middle")
    svg.text(600, 530, "completion > 400 characters → subtract 0.05 · pass threshold ≥ 0.95", cls="small mono muted", anchor="middle")
    return svg.finish()


def build_heldout_selection() -> str:
    report = json.loads((ROOT / HELDOUT_REPORT).read_text())
    checkpoints = report["checkpoints"]
    selected = max(checkpoints, key=lambda item: (item["metrics"]["pass_at_1"], -item["step"]))
    baseline = 0.5833333333333334
    svg = SVG(
        "Held-out checkpoint selection",
        "Eight checkpoints were evaluated on the same sixty unseen rows; step 350 has the highest pass at one.",
        metadata=_metadata([HELDOUT_REPORT], baseline=baseline, selected_step=selected["step"], selected_pass_at_1=selected["metrics"]["pass_at_1"]),
    )
    _header(svg, "FIGURE 5 · HELD-OUT SELECTION", "Ship the best verified checkpoint—not the last one", "Eight checkpoints × 60 unseen prompts × k=8; selection uses held-out pass@1 only.")
    svg.rect(44, 140, 800, 490, shadow=True)
    x0, y0, cw, ch = 105, 195, 680, 340
    for tick in [0.5, 0.6, 0.7, 0.8]:
        y = y0 + ch - (tick - 0.45) / 0.4 * ch
        svg.add(f'<line x1="{x0}" y1="{y:.2f}" x2="{x0 + cw}" y2="{y:.2f}" stroke="{LINE}"/>')
        svg.text(x0 - 12, y + 4, f"{tick:.1f}", cls="tiny mono muted", anchor="end")
    baseline_y = y0 + ch - (baseline - 0.45) / 0.4 * ch
    svg.add(f'<line x1="{x0}" y1="{baseline_y:.2f}" x2="{x0 + cw}" y2="{baseline_y:.2f}" stroke="{CORAL}" stroke-width="2" stroke-dasharray="8 7"/>')
    svg.text(x0 + cw - 4, baseline_y - 8, "baseline 0.5833", cls="tiny mono coral", anchor="end")
    points: list[tuple[float, float]] = []
    for index, item in enumerate(checkpoints):
        x = x0 + index / (len(checkpoints) - 1) * cw
        value = item["metrics"]["pass_at_1"]
        y = y0 + ch - (value - 0.45) / 0.4 * ch
        points.append((x, y))
    svg.add(_polyline(points, BLUE, width=4))
    for (x, y), item in zip(points, checkpoints, strict=True):
        chosen = item["step"] == selected["step"]
        color = GREEN if chosen else BLUE
        radius = 9 if chosen else 6
        svg.add(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius}" fill="{color}" stroke="#fff" stroke-width="3"/>')
        svg.text(x, y - 15, f'{item["metrics"]["pass_at_1"]:.3f}', cls="tiny mono", anchor="middle", fill=color)
        svg.text(x, y0 + ch + 28, item["step"], cls="tiny mono muted", anchor="middle")
    svg.text(x0 + cw / 2, 588, "checkpoint step", cls="small mono muted", anchor="middle")
    svg.rect(878, 140, 278, 490, fill="#f0faf7", stroke="#bfe6da", shadow=True)
    svg.text(910, 190, "SELECTED", cls="label green")
    svg.text(910, 248, "step 350", cls="hero")
    svg.text(910, 289, "pass@1  0.7833", cls="body mono")
    svg.text(910, 320, "pass@8  0.9000", cls="body mono")
    svg.multiline(910, 382, ["Rule", "1. maximize held-out pass@1", "2. lower step breaks ties"], cls="small", gap=26)
    svg.rect(910, 500, 210, 78, fill="#ffffff", stroke="#bfe6da")
    svg.multiline(1015, 529, ["step 400 fell to 0.7167", "so “latest” was rejected"], cls="tiny", gap=20, anchor="middle")
    return svg.finish()


def build_system_loop() -> str:
    serving = json.loads((ROOT / SERVING_EVIDENCE).read_text())
    capacity = json.loads((ROOT / CAPACITY_EVIDENCE).read_text())
    svg = SVG(
        "VerifierForge six-step closed loop",
        "Traffic discovery, Agent advice, human approval, disposable training, held-out proof, and guarded scale-to-zero serving share Supabase and S3 evidence.",
        height=730,
        metadata=_metadata(
            [SERVING_EVIDENCE, CAPACITY_EVIDENCE],
            cold_starts=[serving["cycle_one"]["cold_start_seconds"], serving["cycle_two"]["cold_start_seconds"]],
            selected_gpu=capacity["selected"],
        ),
    )
    _header(svg, "FIGURE 6 · SYSTEM ARCHITECTURE", "One loop, three systems, disposable compute", "Agent decides, Supabase remembers, Provisioner spends and tears down.")
    svg.rect(44, 140, 1112, 525, shadow=True)
    steps = [
        ("01", "DISCOVER", "cluster traffic\n+ cost + volume", BLUE),
        ("02", "ANALYZE", "GPT-5.6 +\n read-only tools", BLUE),
        ("03", "APPROVE", "human intent +\n explicit Start", AMBER),
        ("04", "FORGE", "capacity-aware\n disposable GPU", GREEN),
        ("05", "PROVE", "held-out select +\n spurious control", GREEN),
        ("06", "SHIP", "canary + guardian\n + idle reap", GRAPHITE),
    ]
    xs = [90, 280, 470, 660, 850, 1040]
    for index, ((number, title, body, color), x) in enumerate(zip(steps, xs, strict=True)):
        svg.add(f'<circle cx="{x}" cy="280" r="70" fill="#f8fbfc" stroke="{color}" stroke-width="3"/>')
        svg.text(x, 257, number, cls="label", anchor="middle", fill=color)
        svg.text(x, 286, title, cls="label", anchor="middle")
        svg.multiline(x, 315, body.split("\n"), cls="tiny muted", gap=17, anchor="middle")
        if index < len(xs) - 1:
            svg.arrow(x + 72, 280, xs[index + 1] - 72, 280, color=color)
    svg.rect(95, 405, 285, 150, fill="#eef6ff", stroke="#c8e1f8")
    svg.text(122, 440, "FORGE AGENT", cls="label blue")
    svg.multiline(122, 477, ["decision + strict config", "Gate C: 1 / 1 / 0 / 1", "no execution handle"], cls="small", gap=24)
    svg.rect(457, 405, 285, 150, fill="#f0faf7", stroke="#bfe6da")
    svg.text(484, 440, "SUPABASE + S3", cls="label green")
    svg.multiline(484, 477, ["relational facts + state", "immutable curves + traces", "manifest-last identity"], cls="small", gap=24)
    svg.rect(819, 405, 285, 150, fill="#fff4e3", stroke="#f0d7aa")
    svg.text(846, 440, "PROVISIONER", cls="label amber")
    svg.multiline(846, 477, ["live price / capacity", "$0.20/hr selected", "wake → ready → cold"], cls="small", gap=24)
    svg.add(f'<path d="M1040 352 C1040 620, 90 620, 90 352" stroke="{GREEN}" stroke-width="3" fill="none" stroke-dasharray="10 8" marker-end="url(#arrow)"/>')
    svg.text(600, 624, "guardian evidence feeds the next discovery cycle · no GPU is a source of truth", cls="small mono green", anchor="middle")
    return svg.finish()


def build_mark() -> str:
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96" role="img" aria-labelledby="vf-mark-title vf-mark-desc">
<title id="vf-mark-title">VerifierForge mark</title>
<desc id="vf-mark-desc">A graphite VF monogram crossed by a blue verification stroke.</desc>
<rect width="96" height="96" rx="26" fill="#17212b"/>
<path d="M22 25h13l13 35 13-35h13L54 73H42L22 25Z" fill="#fff"/>
<path d="M18 68 39 47l11 10 29-31" fill="none" stroke="#087cf0" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""


def build_wordmark() -> str:
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 620 112" role="img" aria-labelledby="vf-wordmark-title vf-wordmark-desc">
<title id="vf-wordmark-title">VerifierForge</title>
<desc id="vf-wordmark-desc">VerifierForge wordmark with a graphite VF mark and blue verification stroke.</desc>
<rect x="4" y="8" width="96" height="96" rx="26" fill="#17212b"/>
<path d="M26 33h13l13 35 13-35h13L58 81H46L26 33Z" fill="#fff"/>
<path d="m22 76 21-21 11 10 29-31" fill="none" stroke="#087cf0" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>
<text x="124" y="74" font-family="Manrope,Inter,Arial,sans-serif" font-size="54" font-weight="750" letter-spacing="-2.5" fill="#17212b">Verifier</text>
<text x="327" y="74" font-family="Manrope,Inter,Arial,sans-serif" font-size="54" font-weight="750" letter-spacing="-2.5" fill="#087cf0">Forge</text>
</svg>
"""


def outputs() -> dict[Path, str]:
    mark = build_mark()
    figures = {
        "01-spurious-control.svg": build_spurious_control(),
        "02-agent-system.svg": build_agent_system(),
        "03-grpo-loop.svg": build_grpo_loop(),
        "04-verifier-pipeline.svg": build_verifier_pipeline(),
        "05-heldout-selection.svg": build_heldout_selection(),
        "06-system-loop.svg": build_system_loop(),
    }
    result = {
        **{FIGURE_DIR / name: content for name, content in figures.items()},
        BRAND_DIR / "verifierforge-mark.svg": mark,
        BRAND_DIR / "verifierforge-wordmark.svg": build_wordmark(),
        ROOT / "frontend" / "public" / "favicon.svg": mark,
        WEB_CONTENT_DIR / "technical-deep-dive.md": ARTICLE_PATH.read_text(
            encoding="utf-8"
        ),
        WEB_CONTENT_DIR / "verifierforge-wordmark.svg": build_wordmark(),
    }
    result.update(
        {
            WEB_CONTENT_DIR / "figures" / name: content
            for name, content in figures.items()
        }
    )
    return result


def write_outputs() -> None:
    for path, content in outputs().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def check_outputs() -> list[str]:
    failures: list[str] = []
    for path, expected in outputs().items():
        if not path.exists():
            failures.append(f"missing: {path.relative_to(ROOT)}")
        elif path.read_text(encoding="utf-8") != expected:
            failures.append(f"stale: {path.relative_to(ROOT)}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        failures = check_outputs()
        if failures:
            print("\n".join(failures))
            return 1
        print(f"blog assets current: {len(outputs())}")
        return 0
    write_outputs()
    print(f"wrote blog assets: {len(outputs())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
