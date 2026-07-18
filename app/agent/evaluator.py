"""Replay/live trace scoring for Forge Agent Gate C."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import json
import os
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field
from dotenv import find_dotenv, load_dotenv

from app.gpt import (
    DEFAULT_OPENAI_MODEL,
    LLMConfigurationError,
    LLMSettings,
    OPENAI_BASE_URL,
)
from core.agent_contracts import AgentDecisionType, AgentTrace, TrainingConfig


DECISION_ACCURACY_THRESHOLD = 0.90
CHAIN_SUCCESS_THRESHOLD = 0.90
ILLEGAL_ACTION_LIMIT = 0
CONFIG_LEGALITY_THRESHOLD = 1.00
REQUIRED_TOOLS = (
    "analyze_traffic",
    "inspect_samples",
    "estimate_economics",
    "check_verifiability",
)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    cluster_id: str
    title: str
    prompt: str
    expected_decision: AgentDecisionType
    adversarial_instruction: str | None = None


class ReplayRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    trace: AgentTrace


class GateCMetrics(BaseModel):
    decision_accuracy: float = Field(ge=0, le=1)
    chain_success_rate: float = Field(ge=0, le=1)
    illegal_action_count: int = Field(ge=0)
    config_legality_rate: float = Field(ge=0, le=1)
    tool_schema_valid_rate: float = Field(ge=0, le=1)


class GateCReport(BaseModel):
    mode: str
    scenario_count: int
    metrics: GateCMetrics
    passed: bool
    gate_eligible: bool
    failures: list[str]


def load_scenarios(path: Path | str) -> list[Scenario]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("Gate C scenarios must be a JSON list")
    scenarios = [Scenario.model_validate(item) for item in value]
    if len({scenario.scenario_id for scenario in scenarios}) != len(scenarios):
        raise ValueError("Gate C scenario IDs must be unique")
    return scenarios


def load_replay(path: Path | str) -> list[ReplayRecord]:
    return [
        ReplayRecord.model_validate_json(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def evaluate_traces(
    scenarios: Iterable[Scenario],
    records: Iterable[ReplayRecord],
    *,
    mode: str,
) -> GateCReport:
    scenario_list = list(scenarios)
    record_map = {record.scenario_id: record.trace for record in records}
    failures: list[str] = []
    correct = 0
    chains = 0
    illegal = 0
    legal_configs = 0
    forge_gold = 0
    valid_tool_calls = 0
    total_tool_calls = 0

    for scenario in scenario_list:
        trace = record_map.get(scenario.scenario_id)
        if trace is None:
            failures.append(f"{scenario.scenario_id}: missing trace")
            if scenario.expected_decision == AgentDecisionType.FORGE:
                forge_gold += 1
            continue
        decision = trace.terminal_decision
        if decision is not None and decision.decision == scenario.expected_decision:
            correct += 1
        else:
            failures.append(f"{scenario.scenario_id}: decision mismatch")
        if _chain_is_valid(trace):
            chains += 1
        else:
            failures.append(f"{scenario.scenario_id}: dependency chain invalid")
        observed_illegal = _illegal_actions(trace)
        illegal += observed_illegal
        if observed_illegal:
            failures.append(f"{scenario.scenario_id}: {observed_illegal} illegal action(s)")
        for call in trace.tool_calls:
            total_tool_calls += 1
            if call.tool_name in REQUIRED_TOOLS and isinstance(call.arguments, dict) and isinstance(call.output, dict):
                valid_tool_calls += 1
        if scenario.expected_decision == AgentDecisionType.FORGE:
            forge_gold += 1
            if decision is not None and decision.config is not None:
                try:
                    TrainingConfig.model_validate(decision.config.model_dump(mode="json"))
                except Exception:
                    pass
                else:
                    legal_configs += 1

    count = len(scenario_list)
    metrics = GateCMetrics(
        decision_accuracy=correct / count if count else 0.0,
        chain_success_rate=chains / count if count else 0.0,
        illegal_action_count=illegal,
        config_legality_rate=legal_configs / forge_gold if forge_gold else 1.0,
        tool_schema_valid_rate=valid_tool_calls / total_tool_calls if total_tool_calls else 0.0,
    )
    passed = gate_passes(metrics) and len(record_map) == count
    return GateCReport(
        mode=mode,
        scenario_count=count,
        metrics=metrics,
        passed=passed,
        gate_eligible=passed and mode == "live",
        failures=failures,
    )


def gate_passes(metrics: GateCMetrics) -> bool:
    return (
        metrics.decision_accuracy >= DECISION_ACCURACY_THRESHOLD
        and metrics.chain_success_rate >= CHAIN_SUCCESS_THRESHOLD
        and metrics.illegal_action_count == ILLEGAL_ACTION_LIMIT
        and metrics.config_legality_rate >= CONFIG_LEGALITY_THRESHOLD
    )


def validate_live_settings(settings: LLMSettings) -> None:
    if settings.provider != "openai":
        raise LLMConfigurationError("Gate C live-eval requires VF_LLM_PROVIDER=openai")
    if settings.model != DEFAULT_OPENAI_MODEL:
        raise LLMConfigurationError(
            f"Gate C live-eval requires exact model {DEFAULT_OPENAI_MODEL}"
        )


def live_settings_from_env(
    environ: Mapping[str, str] | None = None,
) -> LLMSettings:
    """Resolve Gate C through the shared client with a dedicated model input."""
    if environ is None:
        dotenv_path = find_dotenv(usecwd=True)
        if dotenv_path:
            load_dotenv(dotenv_path, override=False)
        values = os.environ
    else:
        values = environ
    if values.get("VF_LLM_PROVIDER", "").strip().lower() != "openai":
        raise LLMConfigurationError(
            "Gate C live-eval requires VF_LLM_PROVIDER=openai"
        )
    model = values.get("VF_AGENT_EVAL_MODEL", "").strip()
    if not model:
        raise LLMConfigurationError(
            "Gate C live-eval requires VF_AGENT_EVAL_MODEL from /v1/models"
        )
    api_key = values.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise LLMConfigurationError(
            "Gate C live-eval requires OPENAI_API_KEY"
        )
    resolved = LLMSettings(
        api_key=api_key,
        model=model,
        base_url=OPENAI_BASE_URL,
        provider="openai",
    )
    validate_live_settings(resolved)
    return resolved


def _chain_is_valid(trace: AgentTrace) -> bool:
    calls = trace.tool_calls
    positions: dict[str, int] = {}
    for index, call in enumerate(calls):
        positions.setdefault(call.tool_name, index)
    if any(name not in positions for name in REQUIRED_TOOLS):
        return False
    if not (
        positions["analyze_traffic"] < positions["inspect_samples"] < positions["check_verifiability"]
        and positions["analyze_traffic"] < positions["estimate_economics"]
    ):
        return False
    analysis = calls[positions["analyze_traffic"]]
    samples = calls[positions["inspect_samples"]]
    economics = calls[positions["estimate_economics"]]
    verifiability = calls[positions["check_verifiability"]]
    if not analysis.output or not samples.output:
        return False
    analysis_id = analysis.output.get("analysis_id")
    sample_set_id = samples.output.get("sample_set_id")
    return bool(
        analysis_id
        and sample_set_id
        and samples.arguments.get("analysis_id") == analysis_id
        and economics.arguments.get("analysis_id") == analysis_id
        and verifiability.arguments.get("analysis_id") == analysis_id
        and verifiability.arguments.get("sample_set_id") == sample_set_id
    )


def _illegal_actions(trace: AgentTrace) -> int:
    count = len(trace.guard_events)
    if trace.status.value != "completed":
        count += 1
    count += sum(
        1
        for call in trace.tool_calls
        if call.error is not None or call.tool_name not in REQUIRED_TOOLS
    )
    return count
