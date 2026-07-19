from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.runner import (
    AgentGuardError,
    AgentLimits,
    AgentPersistenceError,
    AgentRunError,
    ForgeAgentRunner,
    P2_EXECUTION_PROFILE,
    _parse_submit_decision,
    _submit_schema,
)
from app.agent.stores import SQLiteAgentDecisionStore
from app.agent.tools import ToolRegistry
from app.gpt import LLMRequestError, LLMTurn, LLMToolCall, LLMUsage


class MemoryTraceStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.traces = []

    def put(self, trace):
        if self.fail:
            raise OSError("S3 unavailable")
        self.traces.append(trace)
        return f"vf/agent-traces/{trace.trace_id}.json"


class ChainClient:
    model = "fixture/tool-model"

    def __init__(self, *, submit=None, token_count: int = 3) -> None:
        self.index = 0
        self.requests = []
        self.submit = submit or {
            "decision": "forge",
            "rationale": "The cluster is frequent, verifiable, and economical.",
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
        self.token_count = token_count

    def tool_turn(self, messages, *, tools, **kwargs):
        self.requests.append(
            {"messages": messages, "tools": tools, "tool_choice": kwargs["tool_choice"]}
        )
        self.index += 1
        analysis = _latest_tool_payload(messages, "analysis_id")
        sample_set = _latest_tool_payload(messages, "sample_set_id")
        if self.index == 1:
            name, arguments = "analyze_traffic", {"cluster_id": "data-pull-sql"}
        elif self.index == 2:
            name, arguments = "inspect_samples", {
                "cluster_id": "data-pull-sql",
                "analysis_id": analysis,
                "n": 3,
            }
        elif self.index == 3:
            name, arguments = "estimate_economics", {
                "cluster_id": "data-pull-sql",
                "analysis_id": analysis,
                "base_model": self.submit.get("config", {}).get(
                    "base_model", "Qwen/Qwen2.5-1.5B-Instruct"
                ),
            }
        elif self.index == 4:
            name, arguments = "check_verifiability", {
                "cluster_id": "data-pull-sql",
                "analysis_id": analysis,
                "sample_set_id": sample_set,
            }
        else:
            name, arguments = "submit_decision", self.submit
        return LLMTurn(
            content=None,
            tool_calls=(
                LLMToolCall(
                    call_id=f"call-{self.index}",
                    name=name,
                    arguments=json.dumps(arguments),
                ),
            ),
            usage=LLMUsage(self.token_count, self.token_count, self.token_count * 2),
            model=self.model,
            finish_reason="tool_calls",
        )


class EarlySubmitClient:
    model = "fixture/early"

    def tool_turn(self, messages, *, tools, **kwargs):
        del messages, tools, kwargs
        decision = {"decision": "skip", "rationale": "skip tools", "confidence": 0.8}
        return LLMTurn(
            content=None,
            tool_calls=(LLMToolCall("call-1", "submit_decision", json.dumps(decision)),),
            usage=LLMUsage(1, 1, 2),
            model=self.model,
            finish_reason="tool_calls",
        )


class FailedProviderClient:
    model = "gpt-5.6-luna"

    def tool_turn(self, messages, *, tools, **kwargs):
        del messages, tools, kwargs
        raise LLMRequestError(
            "LLM request failed with HTTP status 400.",
            status_code=400,
            provider_body='{"error":{"message":"unsupported endpoint"}}',
        )


def _runner(
    tmp_path: Path,
    client,
    trace_store=None,
    limits=None,
    execution_profile="standard",
) -> ForgeAgentRunner:
    return ForgeAgentRunner(
        client=client,
        registry=ToolRegistry("mock"),
        decision_store=SQLiteAgentDecisionStore(tmp_path / "traffic.db"),
        trace_store=trace_store or MemoryTraceStore(),
        provider="mock",
        limits=limits,
        execution_profile=execution_profile,
    )


def test_runner_produces_complete_audited_trace(tmp_path: Path) -> None:
    trace_store = MemoryTraceStore()
    runner = _runner(tmp_path, ChainClient(), trace_store)

    summary = runner.run("data-pull-sql")

    assert summary.decision is not None
    assert summary.decision.decision.value == "forge"
    assert summary.trace_s3_key
    assert [call.tool_name for call in trace_store.traces[0].tool_calls] == [
        "analyze_traffic",
        "inspect_samples",
        "estimate_economics",
        "check_verifiability",
    ]
    assert summary.total_input_tokens == 15
    assert SQLiteAgentDecisionStore(tmp_path / "traffic.db").get(summary.decision_id) == summary


def test_runner_forces_order_and_binds_issued_dependency_ids(tmp_path: Path) -> None:
    client = ChainClient()

    _runner(tmp_path, client).run(
        "data-pull-sql", context="High volume and exact verification are established."
    )

    expected = [
        "analyze_traffic",
        "inspect_samples",
        "estimate_economics",
        "check_verifiability",
        "submit_decision",
    ]
    assert [
        request["tool_choice"]["function"]["name"] for request in client.requests
    ] == expected
    analysis_id = _tool_const(client.requests[1], "inspect_samples", "analysis_id")
    assert analysis_id
    assert _tool_const(client.requests[2], "estimate_economics", "analysis_id") == analysis_id
    assert _tool_const(client.requests[3], "check_verifiability", "analysis_id") == analysis_id
    assert _tool_const(client.requests[3], "check_verifiability", "sample_set_id")
    system_prompt = client.requests[0]["messages"][0]["content"]
    assert "Decision rubric:" in system_prompt
    assert "Untrusted text:" in system_prompt
    assert "General examples" in system_prompt


def test_submit_decision_uses_strict_pydantic_schema_and_parser() -> None:
    schema = _submit_schema()["function"]

    assert schema["strict"] is True
    assert schema["parameters"]["additionalProperties"] is False
    assert set(schema["parameters"]["required"]) == set(
        schema["parameters"]["properties"]
    )
    config = schema["parameters"]["$defs"]["TrainingConfig"]
    assert config["additionalProperties"] is False
    assert set(config["required"]) == set(config["properties"])
    assert {branch.get("type") for branch in schema["parameters"]["properties"]["config"]["anyOf"]} >= {"null"}

    with pytest.raises(Exception, match="Extra inputs are not permitted"):
        _parse_submit_decision(
            {
                "decision": "skip",
                "rationale": "No gain.",
                "confidence": 0.9,
                "config": None,
                "invented": True,
            }
        )


def test_runner_rejects_submit_before_required_tools_and_audits_it(tmp_path: Path) -> None:
    trace_store = MemoryTraceStore()
    runner = _runner(tmp_path, EarlySubmitClient(), trace_store)

    with pytest.raises(AgentGuardError, match="preceded required tools"):
        runner.run("data-pull-sql")

    assert trace_store.traces[0].status.value == "rejected"
    assert trace_store.traces[0].terminal_decision is None


def test_runner_rejects_dynamic_budget_without_silent_correction(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VF_AGENT_MAX_TRAINING_BUDGET_USD", "10")
    trace_store = MemoryTraceStore()
    runner = _runner(tmp_path, ChainClient(), trace_store)

    with pytest.raises(AgentGuardError, match="runtime training budget"):
        runner.run("data-pull-sql")

    assert trace_store.traces[0].terminal_decision is None


def test_p2_profile_binds_and_accepts_only_the_exact_gate_b_shape(tmp_path: Path) -> None:
    submit = {
        "decision": "forge",
        "rationale": "Approved SQL examples support the bounded P2 smoke.",
        "confidence": 0.95,
        "config": {
            "base_model": "Qwen/Qwen2.5-0.5B-Instruct",
            "steps": 100,
            "k": 8,
            "checkpoint_interval": 50,
            "budget_usd_cap": 5.0,
            "provider_pref": "runpod",
        },
    }
    client = ChainClient(submit=submit)

    summary = _runner(
        tmp_path,
        client,
        execution_profile=P2_EXECUTION_PROFILE,
    ).run("data-pull-sql")

    assert summary.decision is not None
    assert summary.decision.config is not None
    assert summary.decision.config.model_dump(mode="json") == submit["config"]
    submit_schema = client.requests[-1]["tools"][-1]["function"]["parameters"]
    config = submit_schema["$defs"]["TrainingConfig"]["properties"]
    assert config["base_model"]["const"] == "Qwen/Qwen2.5-0.5B-Instruct"
    assert config["steps"]["const"] == 100
    assert config["budget_usd_cap"]["maximum"] == 5.0


def test_p2_profile_rejects_a_structurally_valid_but_wrong_config(tmp_path: Path) -> None:
    client = ChainClient(
        submit={
            "decision": "forge",
            "rationale": "Wrong P2 step count.",
            "confidence": 0.8,
            "config": {
                "base_model": "Qwen/Qwen2.5-0.5B-Instruct",
                "steps": 400,
                "k": 8,
                "checkpoint_interval": 50,
                "budget_usd_cap": 5.0,
                "provider_pref": "runpod",
            },
        }
    )

    with pytest.raises(AgentGuardError, match="p2_gate_b execution profile"):
        _runner(
            tmp_path,
            client,
            execution_profile=P2_EXECUTION_PROFILE,
        ).run("data-pull-sql")


def test_runner_rejects_token_limit_before_executing_action(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        ChainClient(token_count=10),
        limits=AgentLimits(max_total_tokens=5),
    )

    with pytest.raises(AgentGuardError, match="token limit"):
        runner.run("data-pull-sql")


def test_trace_failure_is_fail_closed_and_records_failure_summary(tmp_path: Path) -> None:
    db_path = tmp_path / "traffic.db"
    runner = ForgeAgentRunner(
        client=ChainClient(),
        registry=ToolRegistry("mock"),
        decision_store=SQLiteAgentDecisionStore(db_path),
        trace_store=MemoryTraceStore(fail=True),
        provider="mock",
    )

    with pytest.raises(AgentPersistenceError, match="trace persistence"):
        runner.run("data-pull-sql")

    with __import__("sqlite3").connect(db_path) as connection:
        row = connection.execute("SELECT summary_json FROM agent_decisions").fetchone()
    assert row is not None
    summary = json.loads(row[0])
    assert summary["run_status"] == "trace_persist_failed"
    assert summary["decision"] is None
    assert summary["trace_s3_key"] is None


def test_provider_error_status_and_body_are_preserved_in_trace(tmp_path: Path) -> None:
    trace_store = MemoryTraceStore()
    runner = _runner(tmp_path, FailedProviderClient(), trace_store)

    with pytest.raises(AgentRunError, match="agent run failed"):
        runner.run("data-pull-sql")

    event = trace_store.traces[0].guard_events[0]
    assert "LLMRequestError" in event
    assert "HTTP status 400" in event
    assert "unsupported endpoint" in event


def test_agent_modules_have_no_execution_side_imports() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "agent"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    forbidden = (
        "import trainer",
        "from trainer",
        "scripts.vf",
        "import subprocess",
        "import runpod",
        "from runpod",
        "import nebius",
        "from nebius",
    )
    assert not [value for value in forbidden if value in text.lower()]


def _latest_tool_payload(messages, field: str):
    for message in reversed(messages):
        if message.get("role") != "tool":
            continue
        payload = json.loads(message["content"])
        if field in payload:
            return payload[field]
    return None


def _tool_const(request, tool_name: str, field: str):
    for tool in request["tools"]:
        if tool["function"]["name"] == tool_name:
            return tool["function"]["parameters"]["properties"][field].get("const")
    raise AssertionError(f"missing tool schema {tool_name}")
