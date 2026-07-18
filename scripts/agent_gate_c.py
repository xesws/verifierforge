"""Score Forge Agent replay evidence; live mode is fail-closed at preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from app.agent.evaluator import (
    ReplayRecord,
    evaluate_traces,
    live_settings_from_env,
    load_replay,
    load_scenarios,
    run_live_preflight,
)
from app.agent.runner import AgentRunError, ForgeAgentRunner
from app.agent.stores import S3AgentTraceStore, SQLiteAgentDecisionStore
from app.agent.tools import ToolRegistry
from app.gpt import LLMClient, LLMSettings
from app.gpt.budget import CostLedger, LLMBudgetError
from app.proxy.traffic import DEFAULT_DB_PATH
import os


LIVE_ROUND_RESERVATION_USD = 1.50
LIVE_ROUND_LIMIT = 3
LIVE_STATUS_PREFIX = "gate_c_v0223_round_"


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
        scenarios = load_scenarios(args.scenarios)
        replay_report = evaluate_traces(
            scenarios, load_replay(args.replay), mode="replay"
        )
        if not replay_report.passed:
            raise RuntimeError("paid Gate C blocked because replay-eval did not pass")
        settings = live_settings_from_env()
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
    completed_rounds = ledger.count_status_prefix("openai", LIVE_STATUS_PREFIX)
    if completed_rounds >= LIVE_ROUND_LIMIT:
        raise LLMBudgetError(
            f"Gate C v0.22.3 live round limit reached ({LIVE_ROUND_LIMIT})"
        )
    round_number = completed_rounds + 1
    reservation = LIVE_ROUND_RESERVATION_USD
    status_prefix = f"{LIVE_STATUS_PREFIX}{round_number}_"
    ledger.authorize("openai", reservation)
    try:
        preflight_usage = run_live_preflight(LLMClient(settings))
    except Exception:
        ledger.record(
            provider="openai",
            reservation_usd=reservation,
            provider_reported_cost_usd=None,
            model=settings.model,
            input_tokens=0,
            output_tokens=0,
            status=status_prefix + "preflight_failed",
        )
        raise
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
        input_tokens=preflight_usage.input_tokens
        + sum(record.trace.total_input_tokens for record in records),
        output_tokens=preflight_usage.output_tokens
        + sum(record.trace.total_output_tokens for record in records),
        status=status_prefix
        + (
            "runner_failed"
            if failed or len(records) != len(scenarios)
            else "transport_completed"
        ),
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
