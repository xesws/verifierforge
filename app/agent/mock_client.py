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
        del tools, kwargs
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
            }
        elif self.turn == 4:
            name, arguments = "check_verifiability", {
                "cluster_id": self.cluster_id,
                "analysis_id": analysis_id,
                "sample_set_id": sample_set_id,
            }
        else:
            name, arguments = "submit_decision", _decision(self.cluster_id)
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


def _decision(cluster_id: str) -> dict[str, Any]:
    if cluster_id == "data-pull-sql":
        return {
            "decision": "forge",
            "rationale": "High SQL volume, deterministic verification, and positive payback support a small-model forge.",
            "confidence": 0.94,
            "config": {
                "base_model": "Qwen/Qwen2.5-1.5B-Instruct",
                "steps": 400,
                "k": 8,
                "checkpoint_interval": 50,
                "budget_usd_cap": 25.0,
                "provider_pref": "auto",
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
