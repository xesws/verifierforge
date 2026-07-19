"""Deterministic read-only tools and real/mock bindings for Forge Agent."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
from statistics import median
from typing import Any, Callable, Literal

from app.db import RepositoryGateway, repository_gateway
from app.db.settings import DatabaseBackend, DatabaseSettings
from app.agent.sample_sources import validate_approved_source
from app.proxy.clusters import (
    SYSTEM_PROMPTS_BY_CLUSTER,
    cluster_profile,
    system_prompt_hash,
)
from app.proxy.traffic import DEFAULT_DB_PATH
from core.agent_contracts import (
    ALLOWED_BASE_MODELS,
    P2_BASE_MODEL,
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
from core.contracts import ApprovedSampleSource
from core.rewards.nl2sql import NL2SQLVerifier


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
        gateway: RepositoryGateway | None = None,
    ) -> None:
        if binding not in {"real", "mock"}:
            raise ValueError("tool binding must be real or mock")
        self.binding = binding
        self._db_path_explicit = db_path is not None
        self.db_path = Path(
            db_path
            if db_path is not None
            else os.environ.get("VF_PROXY_DB_PATH", str(DEFAULT_DB_PATH))
        ).expanduser()
        self.gateway = gateway
        self._analyses: dict[str, AnalyzeTrafficOutput] = {}
        self._sample_sets: dict[str, InspectSamplesOutput] = {}
        self._sample_verifier_results: dict[str, list[bool]] = {}

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
            else _real_analysis(
                request.cluster_id,
                self.db_path,
                self.gateway,
                db_path_explicit=self._db_path_explicit,
            )
        )
        self._analyses[output.analysis_id] = output
        return output

    def _inspect_samples(self, request: InspectSamplesInput) -> InspectSamplesOutput:
        analysis = self._require_analysis(request.cluster_id, request.analysis_id)
        verifier_results: list[bool] = []
        if self.binding == "mock":
            samples = _mock_samples(request.cluster_id, request.n)
        else:
            source = _real_approved_source(
                request.cluster_id,
                self.db_path,
                self.gateway,
                db_path_explicit=self._db_path_explicit,
            )
            records = validate_approved_source(source) if source is not None else []
            selected = sorted(records, key=lambda item: str(item["id"]))[: request.n]
            samples = [
                RedactedSample(
                    sample_id=str(record["id"]),
                    request_excerpt=str(record.get("question") or record["prompt"])[:500],
                    response_excerpt=str(record["reference_sql"])[:500],
                )
                for record in selected
            ]
            verifier_results = [_reference_is_verifiable(record) for record in selected]
        sufficient = bool(samples)
        reason = (
            (
                "deterministic redacted fixture samples"
                if self.binding == "mock"
                else "deterministic excerpts from the user-approved sample source"
            )
            if sufficient
            else "traffic metadata stores no bodies and no approved sample source is attached"
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
        self._sample_verifier_results[sample_set_id] = verifier_results
        return output

    def _estimate_economics(
        self, request: EstimateEconomicsInput
    ) -> EstimateEconomicsOutput:
        analysis = self._require_analysis(request.cluster_id, request.analysis_id)
        if request.base_model not in ALLOWED_BASE_MODELS:
            raise ValueError("economics model is outside the Forge Agent whitelist")
        gpu_hours, hourly_price = (
            (1.0, 0.50) if request.base_model == P2_BASE_MODEL else (2.0, 2.50)
        )
        training_cost = gpu_hours * hourly_price
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
                f"training_cost={gpu_hours:g}_gpu_hours*${hourly_price:.2f}; "
                "tuned_monthly=0.30*current_monthly; "
                "savings=current_monthly-tuned_monthly; payback=training_cost/savings"
            ),
            assumptions=[
                f"the selected model run uses {gpu_hours:g} GPU hour(s)",
                f"assumed GPU price is ${hourly_price:.2f}/hour",
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
        if not samples.data_sufficient:
            return CheckVerifiabilityOutput(
                cluster_id=request.cluster_id,
                analysis_id=request.analysis_id,
                sample_set_id=request.sample_set_id,
                data_sufficient=False,
                confidence=0.0,
                reasons=["no approved request/response samples are available"],
            )
        if self.binding == "real":
            results = self._sample_verifier_results.get(request.sample_set_id, [])
            confidence = sum(results) / len(results) if results else 0.0
            return CheckVerifiabilityOutput(
                cluster_id=request.cluster_id,
                analysis_id=request.analysis_id,
                sample_set_id=request.sample_set_id,
                data_sufficient=bool(results),
                confidence=confidence,
                reasons=[
                    f"{sum(results)}/{len(results)} approved reference SQL samples pass NL2SQLVerifier v{NL2SQLVerifier.VERSION}",
                    "each inspected record includes schema SQL and deterministic expected results",
                ],
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


def _real_analysis(
    cluster_id: str,
    db_path: Path,
    gateway: RepositoryGateway | None,
    *,
    db_path_explicit: bool,
) -> AnalyzeTrafficOutput:
    rows: list[tuple[Any, ...]] = []
    source: ApprovedSampleSource | None = None
    if cluster_id in SYSTEM_PROMPTS_BY_CLUSTER:
        resolved = _resolve_gateway(
            db_path, gateway, db_path_explicit=db_path_explicit
        )
        if resolved is not None:
            try:
                prompt_hash = system_prompt_hash(SYSTEM_PROMPTS_BY_CLUSTER[cluster_id])
                records = resolved.call(
                    lambda repositories: repositories.traffic.list_for_prompt_hash(
                        prompt_hash, limit=10_000
                    )
                )
                rows = [
                    (
                        record.ts.isoformat(),
                        record.tokens_in,
                        record.tokens_out,
                        record.latency_ms,
                        record.cost_usd,
                        record.route_taken,
                    )
                    for record in records
                ]
                source = _source_from_gateway(resolved, cluster_id)
            except (OSError, RuntimeError, ValueError):
                rows = []
    try:
        profile = cluster_profile(cluster_id)
    except KeyError:
        profile = None
    fingerprint = _digest(
        {
            "cluster_id": cluster_id,
            "rows": rows,
            "profile": profile.model_dump(mode="json") if profile else None,
            "approved_sample_source": (
                source.model_dump(mode="json") if source is not None else None
            ),
        }
    )
    latencies = sorted(float(row[3]) for row in rows)
    timestamps = [_parse_timestamp(str(row[0])) for row in rows]
    timestamps = [value for value in timestamps if value is not None]
    count = len(rows)
    cost = sum(float(row[4]) for row in rows)
    return AnalyzeTrafficOutput(
        cluster_id=cluster_id,
        analysis_id=_digest(
            {"binding": "real", "cluster_id": cluster_id, "fingerprint": fingerprint}
        ),
        evidence_fingerprint=fingerprint,
        data_sufficient=count > 0,
        request_count=count,
        monthly_calls=profile.monthly_calls if profile else count,
        monthly_cost_usd=profile.monthly_cost_usd if profile else cost,
        latency_p50_ms=float(median(latencies)) if latencies else 0.0,
        latency_p95_ms=_percentile(latencies, 0.95),
        growth_rate=_growth_rate(count),
        observed_from=min(timestamps) if timestamps else None,
        observed_to=max(timestamps) if timestamps else None,
    )


def _resolve_gateway(
    db_path: Path,
    gateway: RepositoryGateway | None,
    *,
    db_path_explicit: bool,
) -> RepositoryGateway | None:
    if gateway is not None:
        return gateway
    if db_path_explicit:
        settings = DatabaseSettings.sqlite(db_path) if db_path.is_file() else None
    else:
        settings = DatabaseSettings.from_env()
        if settings.backend is DatabaseBackend.SQLITE and not db_path.is_file():
            settings = None
        elif settings.backend is DatabaseBackend.SQLITE:
            settings = DatabaseSettings.sqlite(db_path)
    return repository_gateway(settings) if settings is not None else None


def _source_from_gateway(
    gateway: RepositoryGateway, cluster_id: str
) -> ApprovedSampleSource | None:
    record = gateway.call(lambda repositories: repositories.clusters.get(cluster_id))
    if record is None or record.approved_sample_source is None:
        return None
    return ApprovedSampleSource.model_validate(record.approved_sample_source)


def _real_approved_source(
    cluster_id: str,
    db_path: Path,
    gateway: RepositoryGateway | None,
    *,
    db_path_explicit: bool,
) -> ApprovedSampleSource | None:
    resolved = _resolve_gateway(
        db_path, gateway, db_path_explicit=db_path_explicit
    )
    return _source_from_gateway(resolved, cluster_id) if resolved is not None else None


def _reference_is_verifiable(record: dict[str, Any]) -> bool:
    try:
        verifier = NL2SQLVerifier(record["schema_sql"], record["expected_results"])
        return verifier.score(str(record["prompt"]), str(record["reference_sql"])) == 1.0
    except (KeyError, TypeError, ValueError):
        return False


def _mock_analysis(cluster_id: str) -> AnalyzeTrafficOutput:
    try:
        profile = cluster_profile(cluster_id)
    except KeyError:
        profile = None
    latency, growth = {
        "data-pull-sql": (430.0, 0.18),
        "invoice-field-extraction": (510.0, 0.08),
        "support-ticket-extraction": (360.0, -0.02),
    }.get(cluster_id, (0.0, 0.0))
    fixture = (
        profile.monthly_calls if profile else 0,
        profile.monthly_cost_usd if profile else 0.0,
        latency,
        growth,
    )
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
