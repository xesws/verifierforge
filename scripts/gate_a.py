#!/usr/bin/env python3
"""Run the fixed D3 Gate A difficulty checks over candidate NL2SQL JSONL."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import urlsplit, urlunsplit


# Keep repository imports and the repository-local ignored `.env` available
# without requiring an installed package or a particular caller working dir.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from core.eval_runner import (  # noqa: E402 - path setup above is intentional.
    CompletionError,
    EvaluationMetrics,
    EvaluationRecordError,
    evaluate_records,
    parse_evaluation_record,
)


PASS_AT_1_MIN = 0.20
PASS_AT_1_MAX = 0.60
MIXED_FRACTION_MIN = 0.30


def build_parser() -> argparse.ArgumentParser:
    """Build the stable command-line interface used by the freeze workflow."""
    parser = argparse.ArgumentParser(
        description="Evaluate NL2SQL candidate JSONL against fixed Gate A thresholds."
    )
    parser.add_argument(
        "candidates",
        nargs="?",
        type=Path,
        help="candidate JSONL containing prompt, schema_sql, and expected_results",
    )
    parser.add_argument(
        "--input",
        "--candidates",
        dest="input_path",
        type=Path,
        help="candidate JSONL (alternative to the positional path)",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=8,
        help="independent completions per prompt (default: 8)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="maximum concurrent completion requests (default: 8)",
    )
    parser.add_argument(
        "--report",
        "--evidence",
        dest="report",
        type=Path,
        required=True,
        help="write a structured, secret-free Gate A evidence JSON file",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run Gate A and return a shell-friendly status code.

    Exit status 1 is reserved for a measured difficulty-gate rejection.  Input,
    configuration, and evaluation failures use status 2 and avoid rendering
    provider exception details, which could contain sensitive request context.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    candidate_path = _resolve_candidate_path(parser, args)

    try:
        records, input_digest = load_candidate_jsonl(candidate_path)
    except (OSError, EvaluationRecordError) as error:
        print(f"gate_a input error: {error}", file=sys.stderr)
        return 2

    try:
        client, settings = _load_eval_client()
        model = settings.model
        resolved_base_url = _safe_base_url(settings.base_url)
        print(
            json.dumps(
                {
                    "event": "gate_a_started",
                    "resolved_base_url": resolved_base_url,
                    "resolved_model": model,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        run = evaluate_records(
            records,
            client,
            k=args.k,
            model=model,
            temperature=1.0,
            workers=args.workers,
        )
    except (ValueError, EvaluationRecordError) as error:
        print(f"gate_a configuration error: {error}", file=sys.stderr)
        return 2
    except CompletionError:
        print("gate_a evaluation error: completion request failed", file=sys.stderr)
        return 2
    except Exception:
        # Provider exceptions sometimes include request details.  Gate A never
        # prints them because the caller's environment contains the API key.
        print("gate_a configuration error: LLM client is unavailable", file=sys.stderr)
        return 2

    decision = gate_passes(run.metrics)
    if args.report is not None:
        try:
            write_evidence(
                args.report,
                candidate_path=candidate_path,
                input_digest=input_digest,
                metrics=run.metrics,
                model=model,
                base_url=settings.base_url,
                workers=args.workers,
            )
        except OSError:
            print("gate_a evidence error: could not write evidence file", file=sys.stderr)
            return 2

    print(json.dumps(display_metrics(run.metrics), sort_keys=True))
    return 0 if decision else 1


def load_candidate_jsonl(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Load and validate candidate JSONL before any completion request is made."""
    content = path.read_bytes()
    records: list[dict[str, Any]] = []
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise EvaluationRecordError(f"{path} must be UTF-8 JSONL") from error

    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            raw_record = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise EvaluationRecordError(
                f"{path} line {line_number} is not valid JSON"
            ) from error
        if not isinstance(raw_record, Mapping):
            raise EvaluationRecordError(f"{path} line {line_number} must be a JSON object")
        parsed = parse_evaluation_record(raw_record, source=f"{path} line {line_number}")
        records.append(
            {
                "id": parsed.record_id,
                "prompt": parsed.prompt,
                "schema_sql": parsed.schema_sql,
                "expected_results": [list(row) for row in parsed.expected_results],
            }
        )

    if not records:
        raise EvaluationRecordError(f"{path} contains no evaluation records")
    return records, hashlib.sha256(content).hexdigest()


def gate_passes(metrics: EvaluationMetrics) -> bool:
    """Apply the human-set Gate A thresholds without any adaptive relaxation."""
    return (
        PASS_AT_1_MIN <= metrics.baseline_pass_at_1 <= PASS_AT_1_MAX
        and metrics.mixed_fraction >= MIXED_FRACTION_MIN
    )


def display_metrics(metrics: EvaluationMetrics) -> dict[str, float]:
    """Return exactly the three measured numbers Gate A reports to operators."""
    return {
        "pass_at_1": metrics.baseline_pass_at_1,
        f"pass_at_{metrics.k}": metrics.pass_at_k,
        "mixed_fraction": metrics.mixed_fraction,
    }


def write_evidence(
    path: Path,
    *,
    candidate_path: Path,
    input_digest: str,
    metrics: EvaluationMetrics,
    model: str,
    base_url: object,
    workers: int,
) -> None:
    """Atomically write freeze-ready evidence without prompts, completions, or keys."""
    payload = {
        "schema_version": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "candidate_path": str(candidate_path),
        "input_sha256": input_digest,
        "verifier": {
            "identity": "core.rewards.nl2sql.NL2SQLVerifier",
            "source_sha256": _verifier_source_digest(),
            "full_pass_score": 1.0,
        },
        "model": model,
        "base_url": _safe_base_url(base_url),
        "resolved_config": {
            "base_url": _safe_base_url(base_url),
            "model": model,
        },
        "pass_at_1": metrics.baseline_pass_at_1,
        "pass_at_k": metrics.pass_at_k,
        "mixed_fraction": metrics.mixed_fraction,
        "k": metrics.k,
        "workers": workers,
        "candidate_count": metrics.record_count,
        "sample_count": metrics.record_count * metrics.k,
        "thresholds": {
            "baseline_pass_at_1": [PASS_AT_1_MIN, PASS_AT_1_MAX],
            "mixed_fraction_min": MIXED_FRACTION_MIN,
        },
        "passed": gate_passes(metrics),
    }
    if metrics.k == 8:
        # Preserve the stable pass_at_k/k fields while making the fixed Gate A
        # metric directly addressable in freeze evidence.
        payload["pass_at_8"] = metrics.pass_at_k
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as temporary:
        json.dump(payload, temporary, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def _resolve_candidate_path(parser: argparse.ArgumentParser, args: argparse.Namespace) -> Path:
    if args.candidates is not None and args.input_path is not None:
        parser.error("provide one candidate path, not both a positional path and --input")
    candidate_path = args.input_path or args.candidates
    if candidate_path is None:
        parser.error("a candidate JSONL path is required")
    return candidate_path


def _load_eval_client() -> tuple[Any, Any]:
    """Construct Gate A's explicit eval client without loading dotenv files."""
    from app.gpt import EvalSettings, LLMClient

    settings = EvalSettings.from_env()
    return LLMClient(settings), settings


def _safe_base_url(value: object) -> str | None:
    """Keep a non-secret endpoint identifier for evidence.

    Explicit credentials, query parameters, and fragments are removed. The
    API path (for example ``/v1``) remains, because it is part of the resolved
    OpenAI-compatible endpoint contract.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return "<configured>"
    if not parsed.scheme or not hostname:
        return "<configured>"

    # ``hostname`` deliberately excludes any userinfo. Bracket IPv6 when
    # reconstructing an authority so the emitted endpoint is unambiguous.
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _verifier_source_digest() -> str:
    verifier_path = REPOSITORY_ROOT / "core" / "rewards" / "nl2sql.py"
    return hashlib.sha256(verifier_path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
