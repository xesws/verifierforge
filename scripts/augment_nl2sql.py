#!/usr/bin/env python3
"""Expand reviewed NL2SQL seeds through the shared environment-configured LLM."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPOSITORY_ROOT / "trainer" / "data" / "nl2sql_v1.jsonl"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate verifier-screened NL2SQL prompt variants."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Seed JSONL (defaults to the reviewed V1 fixture).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Destination candidate JSONL; it is replaced atomically.",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        required=True,
        help="Destination count-only run summary JSON; it is replaced atomically.",
    )
    parser.add_argument(
        "--variants-per-seed",
        type=int,
        default=8,
        help="Maximum candidate variants requested and admitted per seed (default: 8).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Optional model override; otherwise the shared client resolves VF_AUGMENT_MODEL.",
    )
    parser.add_argument(
        "--timebox-minutes",
        type=float,
        default=None,
        help="Stop starting new seed requests after this positive number of minutes.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.variants_per_seed < 1:
        _parser().error("--variants-per-seed must be at least 1")
    if args.timebox_minutes is not None and (
        not math.isfinite(args.timebox_minutes) or args.timebox_minutes <= 0
    ):
        _parser().error("--timebox-minutes must be a positive finite number")
    if args.output.resolve() == args.summary.resolve():
        _parser().error("--output and --summary must be different paths")

    if str(REPOSITORY_ROOT) not in sys.path:
        sys.path.insert(0, str(REPOSITORY_ROOT))

    # Import only when the executable path is used: offline engine tests do not
    # need an SDK client or any environment configuration.
    from app.gpt import LLMClient, LLMSettings
    from core.nl2sql_augmentation import (
        AugmentationInputError,
        augment_seed_cases,
        load_seed_cases,
        write_candidates_jsonl_atomic,
        write_summary_json_atomic,
    )

    try:
        client = LLMClient(LLMSettings.from_env(dotenv_path=REPOSITORY_ROOT / ".env"))
        candidates, summary = augment_seed_cases(
            seeds=load_seed_cases(args.input),
            client=client,
            variants_per_seed=args.variants_per_seed,
            model=args.model,
            timebox_seconds=(
                args.timebox_minutes * 60
                if args.timebox_minutes is not None
                else None
            ),
        )
        output_path = write_candidates_jsonl_atomic(args.output, candidates)
        summary_path = write_summary_json_atomic(args.summary, summary)
    except (AugmentationInputError, OSError, RuntimeError, ValueError) as error:
        # The shared client redacts provider responses/credentials. Keep the
        # command's operational failure equally safe and avoid a traceback that
        # could expose a caller's environment context.
        print(f"augmentation error: {error}", file=sys.stderr)
        return 2
    result = summary.as_dict()
    result["output_path"] = str(output_path)
    result["summary_path"] = str(summary_path)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
