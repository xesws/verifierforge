"""Deterministic tool-calling client for zero-cost product and evaluator demos."""

from __future__ import annotations

import json
from typing import Any

from app.gpt import LLMTurn, LLMToolCall, LLMUsage


class MockAgentClient:
    model = "vf-agent-deterministic-mock"

    def __init__(self, cluster_id: str) -> None:
        self.cluster_id = cluster_id
        self.turn = 0

    def tool_turn(self, messages, *, tools, **kwargs) -> LLMTurn:
        del kwargs
        self.turn += 1
        analysis_id = _latest(messages, "analysis_id")
        sample_set_id = _latest(messages, "sample_set_id")
        if self.turn == 1:
            name, arguments = "analyze_traffic", {"cluster_id": self.cluster_id}
        elif self.turn == 2:
            name, arguments = "inspect_samples", {
                "cluster_id": self.cluster_id,
                "analysis_id": analysis_id,
                "n": 3,
            }
        elif self.turn == 3:
            name, arguments = "estimate_economics", {
                "cluster_id": self.cluster_id,
                "analysis_id": analysis_id,
                "base_model": _tool_const(
                    tools,
                    "estimate_economics",
                    "base_model",
                    "Qwen/Qwen2.5-1.5B-Instruct",
                ),
            }
        elif self.turn == 4:
            name, arguments = "check_verifiability", {
                "cluster_id": self.cluster_id,
                "analysis_id": analysis_id,
                "sample_set_id": sample_set_id,
            }
        else:
            p2_model = _tool_const(
                tools,
                "submit_decision",
                "base_model",
                "Qwen/Qwen2.5-1.5B-Instruct",
            )
            name, arguments = "submit_decision", _decision(
                self.cluster_id,
                p2=p2_model == "Qwen/Qwen2.5-0.5B-Instruct",
                verified=_latest(messages, "data_sufficient") == "True",
            )
        return LLMTurn(
            content=None,
            tool_calls=(
                LLMToolCall(
                    call_id=f"mock-call-{self.turn}",
                    name=name,
                    arguments=json.dumps(arguments, sort_keys=True),
                ),
            ),
            usage=LLMUsage(input_tokens=12, output_tokens=6, total_tokens=18),
            model=self.model,
            finish_reason="tool_calls",
        )


def _decision(
    cluster_id: str, *, p2: bool = False, verified: bool = True
) -> dict[str, Any]:
    if cluster_id == "data-pull-sql":
        if not verified:
            return {
                "decision": "need_more_data",
                "rationale": "Traffic exists, but no approved samples establish programmatic verifiability.",
                "confidence": 0.9,
                "config": None,
            }
        return {
            "decision": "forge",
            "rationale": "High SQL volume, deterministic verification, and positive payback support a small-model forge.",
            "confidence": 0.94,
            "config": {
                "base_model": (
                    "Qwen/Qwen2.5-0.5B-Instruct"
                    if p2
                    else "Qwen/Qwen2.5-1.5B-Instruct"
                ),
                "steps": 100 if p2 else 400,
                "k": 8,
                "checkpoint_interval": 50,
                "budget_usd_cap": 5.0 if p2 else 25.0,
                "provider_pref": "runpod" if p2 else "auto",
            },
        }
    if cluster_id == "support-ticket-extraction":
        return {
            "decision": "skip",
            "rationale": "This cluster already has a tuned live route, so another forge is not justified.",
            "confidence": 0.88,
            "config": None,
        }
    return {
        "decision": "need_more_data",
        "rationale": "More approved samples are required before verifiability can be established.",
        "confidence": 0.82,
        "config": None,
    }


def _latest(messages: list[dict[str, Any]], field: str) -> str | None:
    for message in reversed(messages):
        if message.get("role") != "tool":
            continue
        payload = json.loads(message["content"])
        if field in payload:
            return str(payload[field])
    return None


def _tool_const(
    tools: list[dict[str, Any]],
    tool_name: str,
    field: str,
    default: str,
) -> str:
    for tool in tools:
        function = tool.get("function", {})
        if function.get("name") != tool_name:
            continue
        parameters = function.get("parameters", {})
        if tool_name == "submit_decision":
            properties = parameters.get("$defs", {}).get("TrainingConfig", {}).get(
                "properties", {}
            )
        else:
            properties = parameters.get("properties", {})
        value = properties.get(field, {}).get("const")
        return str(value) if value is not None else default
    return default
