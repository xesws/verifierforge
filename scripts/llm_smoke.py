"""Run one bounded provider-preset smoke without persisting response text."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

from dotenv import dotenv_values

from app.gpt import LLMClient, LLMRequestError, LLMSettings
from app.gpt.budget import CostLedger


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("openrouter", "openai"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, default=Path("runs/forge-agent/llm-cost.jsonl"))
    parser.add_argument("--reservation-usd", type=float, default=0.25)
    args = parser.parse_args()

    values = {key: value for key, value in dotenv_values(".env").items() if value is not None}
    values.update(os.environ)
    values["VF_LLM_PROVIDER"] = args.provider
    # This command verifies the named preset rather than a stale generic alias.
    for name in ("VF_LLM_API_KEY", "VF_LLM_BASE_URL", "VF_LLM_MODEL", "VF_AUGMENT_MODEL"):
        values.pop(name, None)
    settings = LLMSettings.from_env(values)
    ledger = CostLedger(args.ledger)
    ledger.authorize(args.provider, args.reservation_usd)
    try:
        turn = LLMClient(settings).chat_turn(
            [{"role": "user", "content": "Reply with exactly OK."}],
            max_completion_tokens=16,
            timeout=30,
        )
    except Exception as error:
        receipt = ledger.record(
            provider=args.provider,
            reservation_usd=args.reservation_usd,
            provider_reported_cost_usd=None,
            model=settings.model,
            input_tokens=0,
            output_tokens=0,
            status="failed",
        )
        report = {
            "provider": args.provider,
            "base_url": settings.base_url,
            "model": settings.model,
            "status": "failed",
            "error_type": type(error).__name__,
            "http_status": error.status_code if isinstance(error, LLMRequestError) else None,
            "charged_usd": receipt.charged_usd,
            "cost_basis": receipt.cost_basis,
        }
        _atomic_json(args.report, report)
        print(json.dumps(report, sort_keys=True))
        return 2
    content = turn.content or ""
    receipt = ledger.record(
        provider=args.provider,
        reservation_usd=args.reservation_usd,
        provider_reported_cost_usd=turn.usage.provider_reported_cost_usd,
        model=settings.model,
        input_tokens=turn.usage.input_tokens,
        output_tokens=turn.usage.output_tokens,
        status="ok",
    )
    report = {
        "provider": args.provider,
        "base_url": settings.base_url,
        "model": settings.model,
        "input_tokens": turn.usage.input_tokens,
        "output_tokens": turn.usage.output_tokens,
        "response_chars": len(content),
        "response_sha256": sha256(content.encode("utf-8")).hexdigest(),
        "charged_usd": receipt.charged_usd,
        "cost_basis": receipt.cost_basis,
        "provider_reported_cost_usd": receipt.provider_reported_cost_usd,
        "status": "ok",
    }
    _atomic_json(args.report, report)
    print(json.dumps(report, sort_keys=True))
    return 0


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
