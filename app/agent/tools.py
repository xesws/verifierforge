"""Deterministic read-only tools and real/mock bindings for Forge Agent."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from statistics import median
from typing import Any, Callable, Literal

from app.proxy.clusters import SYSTEM_PROMPTS_BY_CLUSTER, system_prompt_hash
from app.proxy.traffic import DEFAULT_DB_PATH
from core.agent_contracts import (
    AnalyzeTrafficInput,
    AnalyzeTrafficOutput,
    CheckVerifiabilityInput,
    CheckVerifiabilityOutput,
    EstimateEconomicsInput,
    EstimateEconomicsOutput,
    InspectSamplesInput,
    InspectSamplesOutput,
    RedactedSample,
)


class ToolDependencyError(ValueError):
    """A model supplied an evidence identifier not issued by this registry."""


_INPUT_MODELS = {
    "analyze_traffic": AnalyzeTrafficInput,
    "inspect_samples": InspectSamplesInput,
    "estimate_economics": EstimateEconomicsInput,
    "check_verifiability": CheckVerifiabilityInput,
}


class ToolRegistry:
    """Bind stable tool signatures to real read-only data or mock fixtures."""

    def __init__(
        self,
        binding: Literal["real", "mock"],
        *,
        db_path: Path | str | None = None,
    ) -> None:
        if binding not in {"real", "mock"}:
            raise ValueError("tool binding must be real or mock")
        self.binding = binding
        self.db_path = Path(
            db_path
            if db_path is not None
            else os.environ.get("VF_PROXY_DB_PATH", str(DEFAULT_DB_PATH))
        ).expanduser()
        self._analyses: dict[str, AnalyzeTrafficOutput] = {}
        self._sample_sets: dict[str, InspectSamplesOutput] = {}

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            input_model = _INPUT_MODELS[name]
        except KeyError as error:
            raise ValueError(f"unknown Forge Agent tool: {name}") from error
        request = input_model.model_validate(arguments)
        output = getattr(self, f"_{name}")(request)
        return output.model_dump(mode="json")

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": _DESCRIPTIONS[name],
                    "parameters": model.model_json_schema(),
                },
            }
            for name, model in _INPUT_MODELS.items()
        ]

    def _analyze_traffic(self, request: AnalyzeTrafficInput) -> AnalyzeTrafficOutput:
        output = (
            _mock_analysis(request.cluster_id)
            if self.binding == "mock"
            else _real_analysis(request.cluster_id, self.db_path)
        )
        self._analyses[output.analysis_id] = output
        return output

    def _inspect_samples(self, request: InspectSamplesInput) -> InspectSamplesOutput:
        analysis = self._require_analysis(request.cluster_id, request.analysis_id)
        samples = (
            _mock_samples(request.cluster_id, request.n)
            if self.binding == "mock"
            else []
        )
        sufficient = bool(samples)
        reason = (
            "deterministic redacted fixture samples"
            if sufficient
            else "traffic metadata schema stores no request or response bodies"
        )
        sample_set_id = _digest(
            {
                "binding": self.binding,
                "cluster_id": request.cluster_id,
                "analysis_id": analysis.analysis_id,
                "samples": [sample.model_dump(mode="json") for sample in samples],
            }
        )
        output = InspectSamplesOutput(
            cluster_id=request.cluster_id,
            analysis_id=analysis.analysis_id,
            sample_set_id=sample_set_id,
            data_sufficient=sufficient,
            reason=reason,
            samples=samples,
        )
        self._sample_sets[sample_set_id] = output
        return output

    def _estimate_economics(
        self, request: EstimateEconomicsInput
    ) -> EstimateEconomicsOutput:
        analysis = self._require_analysis(request.cluster_id, request.analysis_id)
        if request.base_model != "Qwen/Qwen2.5-1.5B-Instruct":
            raise ValueError("economics model is outside the Forge Agent whitelist")
        training_cost = 2.0 * 2.5
        projected = analysis.monthly_cost_usd * 0.30
        savings = max(analysis.monthly_cost_usd - projected, 0.0)
        payback = training_cost / savings if savings > 0 else None
        return EstimateEconomicsOutput(
            cluster_id=request.cluster_id,
            analysis_id=analysis.analysis_id,
            data_sufficient=analysis.data_sufficient,
            training_cost_usd=training_cost,
            current_monthly_cost_usd=analysis.monthly_cost_usd,
            projected_monthly_cost_usd=projected,
            projected_monthly_savings_usd=savings,
            payback_months=payback,
            formula=(
                "training_cost=2_gpu_hours*$2.50; tuned_monthly=0.30*current_monthly; "
                "savings=current_monthly-tuned_monthly; payback=training_cost/savings"
            ),
            assumptions=[
                "one 1.5B training run uses two GPU hours",
                "GPU price is $2.50/hour",
                "tuned inference costs 30% of current inference",
            ],
        )

    def _check_verifiability(
        self, request: CheckVerifiabilityInput
    ) -> CheckVerifiabilityOutput:
        self._require_analysis(request.cluster_id, request.analysis_id)
        samples = self._sample_sets.get(request.sample_set_id)
        if samples is None or samples.cluster_id != request.cluster_id:
            raise ToolDependencyError("sample_set_id was not issued for this cluster")
        if self.binding == "real" or not samples.data_sufficient:
            return CheckVerifiabilityOutput(
                cluster_id=request.cluster_id,
                analysis_id=request.analysis_id,
                sample_set_id=request.sample_set_id,
                data_sufficient=False,
                confidence=0.0,
                reasons=["no approved request/response samples are available"],
            )
        confidence = {
            "data-pull-sql": 0.95,
            "invoice-field-extraction": 0.55,
            "support-ticket-extraction": 0.25,
        }.get(request.cluster_id, 0.4)
        return CheckVerifiabilityOutput(
            cluster_id=request.cluster_id,
            analysis_id=request.analysis_id,
            sample_set_id=request.sample_set_id,
            data_sufficient=True,
            confidence=confidence,
            reasons=["outputs have a stable structured shape", "fixtures include deterministic expectations"],
        )

    def _require_analysis(self, cluster_id: str, analysis_id: str) -> AnalyzeTrafficOutput:
        analysis = self._analyses.get(analysis_id)
        if analysis is None or analysis.cluster_id != cluster_id:
            raise ToolDependencyError("analysis_id was not issued for this cluster")
        return analysis


def _real_analysis(cluster_id: str, db_path: Path) -> AnalyzeTrafficOutput:
    rows: list[tuple[Any, ...]] = []
    if db_path.is_file() and cluster_id in SYSTEM_PROMPTS_BY_CLUSTER:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as connection:
                table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='traffic'"
                ).fetchone()
                if table:
                    prompt_hash = system_prompt_hash(SYSTEM_PROMPTS_BY_CLUSTER[cluster_id])
                    rows = connection.execute(
                        """
                        SELECT timestamp, input_tokens, output_tokens, latency_ms,
                               estimated_cost_usd, route_path
                        FROM traffic WHERE system_prompt_hash = ? ORDER BY id
                        """,
                        (prompt_hash,),
                    ).fetchall()
        except sqlite3.Error:
            rows = []
    fingerprint = _digest({"cluster_id": cluster_id, "rows": rows})
    latencies = sorted(float(row[3]) for row in rows)
    timestamps = [_parse_timestamp(str(row[0])) for row in rows]
    timestamps = [value for value in timestamps if value is not None]
    count = len(rows)
    cost = sum(float(row[4]) for row in rows)
    return AnalyzeTrafficOutput(
        cluster_id=cluster_id,
        analysis_id=_digest({"binding": "real", "cluster_id": cluster_id, "fingerprint": fingerprint}),
        evidence_fingerprint=fingerprint,
        data_sufficient=count > 0,
        request_count=count,
        monthly_calls=count,
        monthly_cost_usd=cost,
        latency_p50_ms=float(median(latencies)) if latencies else 0.0,
        latency_p95_ms=_percentile(latencies, 0.95),
        growth_rate=_growth_rate(count),
        observed_from=min(timestamps) if timestamps else None,
        observed_to=max(timestamps) if timestamps else None,
    )


def _mock_analysis(cluster_id: str) -> AnalyzeTrafficOutput:
    fixture = {
        "data-pull-sql": (95_000, 5_500.0, 430.0, 0.18),
        "invoice-field-extraction": (180_000, 6_000.0, 510.0, 0.08),
        "support-ticket-extraction": (240_000, 4_800.0, 360.0, -0.02),
    }.get(cluster_id, (0, 0.0, 0.0, 0.0))
    fingerprint = _digest({"binding": "mock", "cluster_id": cluster_id, "fixture": fixture})
    return AnalyzeTrafficOutput(
        cluster_id=cluster_id,
        analysis_id=_digest({"cluster_id": cluster_id, "fingerprint": fingerprint}),
        evidence_fingerprint=fingerprint,
        data_sufficient=fixture[0] > 0,
        request_count=fixture[0],
        monthly_calls=fixture[0],
        monthly_cost_usd=fixture[1],
        latency_p50_ms=fixture[2] * 0.65,
        latency_p95_ms=fixture[2],
        growth_rate=fixture[3],
    )


def _mock_samples(cluster_id: str, n: int) -> list[RedactedSample]:
    stems = {
        "data-pull-sql": ("List matching rows", "SELECT ..."),
        "invoice-field-extraction": ("Invoice [REDACTED]", '{"invoice":"[REDACTED]"}'),
        "support-ticket-extraction": ("Ticket [REDACTED]", '{"issue":"[REDACTED]"}'),
    }
    if cluster_id not in stems:
        return []
    request, response = stems[cluster_id]
    return [
        RedactedSample(
            sample_id=f"{cluster_id}-{index + 1}",
            request_excerpt=request,
            response_excerpt=response,
        )
        for index in range(min(n, 3))
    ]


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    index = min(int(round((len(values) - 1) * percentile)), len(values) - 1)
    return float(values[index])


def _growth_rate(count: int) -> float:
    if count < 2:
        return 0.0
    first = count // 2
    second = count - first
    return (second - first) / first if first else 0.0


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


_DESCRIPTIONS: dict[str, str] = {
    "analyze_traffic": "Read aggregate frequency, cost, latency, and growth facts for one cluster.",
    "inspect_samples": "Read approved redacted samples bound to a prior traffic analysis.",
    "estimate_economics": "Estimate training cost and monthly savings with explicit formulas.",
    "check_verifiability": "Assess whether approved samples support a programmatic verifier.",
}
