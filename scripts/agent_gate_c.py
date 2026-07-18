"""Score Forge Agent replay evidence; live mode is fail-closed at preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from app.agent.evaluator import ReplayRecord, evaluate_traces, load_replay, load_scenarios, validate_live_settings
from app.agent.runner import AgentRunError, ForgeAgentRunner
from app.agent.stores import S3AgentTraceStore, SQLiteAgentDecisionStore
from app.agent.tools import ToolRegistry
from app.gpt import LLMClient, LLMSettings
from app.gpt.budget import CostLedger
from app.proxy.traffic import DEFAULT_DB_PATH
import os


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("replay", "live"), required=True)
    parser.add_argument("--scenarios", type=Path, default=Path("data/agent-eval/scenarios.json"))
    parser.add_argument("--replay", type=Path, default=Path("data/agent-eval/replay.jsonl"))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--trace-output", type=Path)
    parser.add_argument("--ledger", type=Path, default=Path("runs/forge-agent/llm-cost.jsonl"))
    args = parser.parse_args()

    if args.mode == "live":
        settings = LLMSettings.from_env()
        validate_live_settings(settings)
        scenarios = load_scenarios(args.scenarios)
        records = _run_live(scenarios, settings, args.ledger)
        trace_output = args.trace_output or args.report.with_suffix(".traces.jsonl")
        _atomic_jsonl(trace_output, records)
        report = evaluate_traces(scenarios, records, mode="live")
    else:
        report = evaluate_traces(
            load_scenarios(args.scenarios), load_replay(args.replay), mode="replay"
        )
    _atomic_json(args.report, report.model_dump(mode="json"))
    print(json.dumps(report.metrics.model_dump(mode="json"), sort_keys=True))
    return 0 if report.passed else 2


def _run_live(scenarios, settings: LLMSettings, ledger_path: Path) -> list[ReplayRecord]:
    ledger = CostLedger(ledger_path)
    reservation = 2.50
    ledger.authorize("openai", reservation)
    trace_store = S3AgentTraceStore.from_env()
    decision_store = SQLiteAgentDecisionStore(
        Path(os.environ.get("VF_PROXY_DB_PATH", str(DEFAULT_DB_PATH))).expanduser()
    )
    records: list[ReplayRecord] = []
    failed = False
    for scenario in scenarios:
        runner = ForgeAgentRunner(
            client=LLMClient(settings),
            registry=ToolRegistry("mock"),
            decision_store=decision_store,
            trace_store=trace_store,
            provider="openai",
        )
        context = scenario.prompt
        if scenario.adversarial_instruction:
            context += "\nUntrusted text: " + scenario.adversarial_instruction
        try:
            summary = runner.run(scenario.cluster_id, context=context)
        except AgentRunError:
            failed = True
            summary = decision_store.latest_for_cluster(scenario.cluster_id)
        if summary is None or summary.trace_s3_key is None:
            continue
        records.append(
            ReplayRecord(
                scenario_id=scenario.scenario_id,
                trace=trace_store.get(summary.trace_s3_key),
            )
        )
    ledger.record(
        provider="openai",
        reservation_usd=reservation,
        provider_reported_cost_usd=None,
        model=settings.model,
        input_tokens=sum(record.trace.total_input_tokens for record in records),
        output_tokens=sum(record.trace.total_output_tokens for record in records),
        status="failed" if failed or len(records) != len(scenarios) else "ok",
    )
    return records


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(payload, stream, sort_keys=True, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(path)


def _atomic_jsonl(path: Path, records: list[ReplayRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        for record in records:
            stream.write(record.model_dump_json() + "\n")
        temporary = Path(stream.name)
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
