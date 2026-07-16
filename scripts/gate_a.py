#!/usr/bin/env python3
"""Run the fixed D3 Gate A difficulty checks over candidate NL2SQL JSONL."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import urlsplit, urlunsplit


# Keep repository imports and the repository-local ignored `.env` available
# without requiring an installed package or a particular caller working dir.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.gpt import LLMConfigurationError  # noqa: E402 - path setup above is intentional.
from core.eval_runner import (  # noqa: E402 - path setup above is intentional.
    CompletionError,
    EvaluationCompletionError,
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
    parser.add_argument(
        "--reference",
        action="store_true",
        help="record metrics without rejecting a completed full-set reference run",
    )
    parser.add_argument(
        "--per-prompt-output",
        type=Path,
        help="reference-only JSONL output with one full-pass count per prompt",
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
    if args.per_prompt_output is not None and not args.reference:
        parser.error("--per-prompt-output requires --reference")
    candidate_path = _resolve_candidate_path(parser, args)
    mode = "reference" if args.reference else "gate"
    input_digest: str | None = None
    records: list[dict[str, Any]] | None = None
    settings: Any | None = None
    try:
        records, input_digest = load_candidate_jsonl(candidate_path)
    except (OSError, EvaluationRecordError) as error:
        _write_failure_or_report(
            args.report,
            candidate_path=candidate_path,
            input_digest=None,
            record_count=None,
            settings=None,
            k=args.k,
            workers=args.workers,
            mode=mode,
            category="input",
            error=error,
        )
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
    except EvaluationCompletionError as error:
        _write_failure_or_report(
            args.report,
            candidate_path=candidate_path,
            input_digest=input_digest,
            record_count=len(records),
            settings=settings,
            k=args.k,
            workers=args.workers,
            mode=mode,
            category="completion",
            error=error,
        )
        print("gate_a evaluation error: terminal completion failure", file=sys.stderr)
        return 2
    except CompletionError as error:
        _write_failure_or_report(
            args.report,
            candidate_path=candidate_path,
            input_digest=input_digest,
            record_count=len(records),
            settings=settings,
            k=args.k,
            workers=args.workers,
            mode=mode,
            category="completion",
            error=error,
        )
        print("gate_a evaluation error: terminal completion failure", file=sys.stderr)
        return 2
    except (LLMConfigurationError, ValueError, EvaluationRecordError) as error:
        _write_failure_or_report(
            args.report,
            candidate_path=candidate_path,
            input_digest=input_digest,
            record_count=len(records),
            settings=settings,
            k=args.k,
            workers=args.workers,
            mode=mode,
            category="configuration",
            error=error,
        )
        print(f"gate_a configuration error: {_safe_message(error)}", file=sys.stderr)
        return 2
    except Exception as error:
        _write_failure_or_report(
            args.report,
            candidate_path=candidate_path,
            input_digest=input_digest,
            record_count=len(records),
            settings=settings,
            k=args.k,
            workers=args.workers,
            mode=mode,
            category="internal",
            error=error,
        )
        # Provider exceptions sometimes include request details. Gate A never
        # renders them; the evidence file contains a bounded redacted chain.
        print("gate_a internal error: see evidence report", file=sys.stderr)
        return 2

    per_prompt_artifact: dict[str, Any] | None = None
    if args.per_prompt_output is not None:
        try:
            per_prompt_artifact = write_per_prompt_pass_counts(
                args.per_prompt_output,
                groups=run.groups,
                k=args.k,
            )
        except (OSError, ValueError) as error:
            _write_failure_or_report(
                args.report,
                candidate_path=candidate_path,
                input_digest=input_digest,
                record_count=len(records),
                settings=settings,
                k=args.k,
                workers=args.workers,
                mode=mode,
                category="artifact",
                error=error,
            )
            print("gate_a artifact error: could not write per-prompt output", file=sys.stderr)
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
                mode=mode,
                per_prompt_artifact=per_prompt_artifact,
            )
        except OSError:
            print("gate_a evidence error: could not write evidence file", file=sys.stderr)
            return 2

    print(json.dumps(display_metrics(run.metrics), sort_keys=True))
    if args.reference:
        return 0
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
    mode: str = "gate",
    per_prompt_artifact: Mapping[str, Any] | None = None,
) -> None:
    """Atomically write freeze-ready evidence without prompts, completions, or keys."""
    payload = {
        "schema_version": 2,
        "status": "completed",
        "mode": mode,
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
        "workers": min(workers, 8),
        "max_in_flight": min(workers, 8),
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
    if per_prompt_artifact is not None:
        payload["per_prompt_pass_counts"] = dict(per_prompt_artifact)
    _write_json_atomic(path, payload)


def write_per_prompt_pass_counts(
    path: Path,
    *,
    groups: Sequence[Any],
    k: int,
) -> dict[str, Any]:
    """Atomically persist only the full-pass count for each evaluated prompt.

    This is intentionally a reference-probe artifact: it identifies the source
    record and its count out of ``k`` without retaining prompt or completion text.
    """
    rows: list[dict[str, int | str | None]] = []
    for record_index, group in enumerate(groups, start=1):
        full_passes = getattr(group, "full_passes", None)
        record_id = getattr(group, "record_id", None)
        if not isinstance(full_passes, tuple) or len(full_passes) != k:
            raise ValueError("per-prompt groups must contain exactly k pass labels")
        if not all(isinstance(value, bool) for value in full_passes):
            raise ValueError("per-prompt groups must contain boolean pass labels")
        pass_count = sum(full_passes)
        if not 0 <= pass_count <= k:
            raise ValueError("per-prompt pass count is outside the sampling range")
        rows.append(
            {
                "record_index": record_index,
                "record_id": record_id if isinstance(record_id, str) else None,
                "pass_count": pass_count,
                "k": k,
            }
        )

    content = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    _write_text_atomic(path, content)
    raw = path.read_bytes()
    histogram = Counter(str(row["pass_count"]) for row in rows)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "record_count": len(rows),
        "histogram": dict(sorted(histogram.items(), key=lambda item: int(item[0]))),
    }


def _write_failure_or_report(
    path: Path,
    *,
    candidate_path: Path,
    input_digest: str | None,
    record_count: int | None,
    settings: object | None,
    k: int,
    workers: int,
    mode: str,
    category: str,
    error: BaseException,
) -> None:
    """Persist a secret-free failure report or surface a durable-write failure."""
    try:
        write_failure_evidence(
            path,
            candidate_path=candidate_path,
            input_digest=input_digest,
            record_count=record_count,
            settings=settings,
            k=k,
            workers=workers,
            mode=mode,
            category=category,
            error=error,
        )
    except OSError:
        print("gate_a evidence error: could not write failure evidence file", file=sys.stderr)


def write_failure_evidence(
    path: Path,
    *,
    candidate_path: Path,
    input_digest: str | None,
    record_count: int | None,
    settings: object | None,
    k: int,
    workers: int,
    mode: str,
    category: str,
    error: BaseException,
) -> None:
    """Write schema-v2 evidence for an invalid Gate A invocation or run."""
    base_url = _safe_base_url(getattr(settings, "base_url", None))
    model = getattr(settings, "model", None)
    resolved_model = model.strip() if isinstance(model, str) and model.strip() else None
    payload: dict[str, Any] = {
        "schema_version": 2,
        "status": "failed",
        "mode": mode,
        "timestamp": datetime.now(UTC).isoformat(),
        "candidate_path": str(candidate_path),
        "input_sha256": input_digest,
        "candidate_count": record_count,
        "verifier": {
            "identity": "core.rewards.nl2sql.NL2SQLVerifier",
            "source_sha256": _verifier_source_digest(),
            "full_pass_score": 1.0,
        },
        "model": resolved_model,
        "base_url": base_url,
        "resolved_config": {
            "base_url": base_url,
            "model": resolved_model,
        },
        "k": k,
        "workers": min(workers, 8),
        "max_in_flight": min(workers, 8),
        "failure": _failure_payload(category, error),
    }
    _write_json_atomic(path, payload)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Durably replace an evidence file, preserving an older complete report on failure."""
    _write_text_atomic(path, json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")


def _write_text_atomic(path: Path, content: str) -> None:
    """Durably replace text output, preserving an older complete file on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _failure_payload(category: str, error: BaseException) -> dict[str, Any]:
    """Return failure facts that are useful for recovery but safe to retain."""
    payload: dict[str, Any] = {
        "category": category,
        "exception_type": type(error).__name__,
        "message": _safe_message(error),
        "exception_chain": _exception_chain(error),
    }
    if isinstance(error, EvaluationCompletionError):
        payload.update(
            {
                "circuit_open": error.circuit_open,
                "completed_logical_samples": error.completed_logical_samples,
                "total_logical_samples": error.total_logical_samples,
                "terminal_failure_count": len(error.failures),
                "maximum_consecutive_terminal_failures": (
                    error.maximum_consecutive_terminal_failures
                ),
                "failure_distribution": dict(
                    sorted(
                        Counter(
                            failure.attempts[-1].exception_type
                            if failure.attempts
                            else type(failure).__name__
                            for failure in error.failures
                        ).items()
                    )
                ),
                "failures": [
                    _completion_failure_payload(failure) for failure in error.failures
                ],
            }
        )
    elif isinstance(error, CompletionError):
        payload.update(
            {
                "circuit_open": False,
                "terminal_failure_count": 1,
                "failures": [_completion_failure_payload(error)],
            }
        )
    return payload


def _completion_failure_payload(error: CompletionError) -> dict[str, Any]:
    """Serialize one retry-exhausted logical sample and its causal chain."""
    return {
        "request_ordinal": error.request_ordinal,
        "record_index": error.record_index,
        "sample_index": error.sample_index,
        "attempt_count": len(error.attempts),
        "attempts": [
            {
                "attempt": attempt.attempt,
                "exception_type": attempt.exception_type,
                "message": attempt.message,
                "status_code": attempt.status_code,
                "provider_body": attempt.provider_body,
            }
            for attempt in error.attempts
        ],
        "exception_chain": _exception_chain(error),
    }


def _exception_chain(error: BaseException, *, limit: int = 8) -> list[dict[str, Any]]:
    """Traverse explicit causes without exposing unredacted provider text."""
    chain: list[dict[str, Any]] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < limit:
        seen.add(id(current))
        entry: dict[str, Any] = {
            "type": type(current).__name__,
            "message": _safe_message(current),
        }
        status_code = getattr(current, "status_code", None)
        if isinstance(status_code, int) and not isinstance(status_code, bool):
            entry["status_code"] = status_code
        provider_body = getattr(current, "provider_body", None)
        if provider_body is None:
            provider_body = getattr(current, "body", None)
        if provider_body is not None:
            entry["provider_body"] = _redact_and_truncate(str(provider_body))
        chain.append(entry)
        next_error = current.__cause__ or current.__context__
        if next_error is None:
            possible_cause = getattr(current, "cause", None)
            next_error = possible_cause if isinstance(possible_cause, BaseException) else None
        current = next_error
    return chain


def _safe_message(error: BaseException) -> str:
    """Keep known non-secret diagnostics while redacting arbitrary provider strings."""
    return _redact_and_truncate(str(error), limit=1024)


def _redact_and_truncate(value: str, *, limit: int = 4096) -> str:
    """Redact bearer/query credentials before any evidence write or terminal use."""
    redacted = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)([^\s,;\"']+)",
        r"\1[REDACTED]",
        value,
    )
    redacted = re.sub(r"(?i)(bearer\s+)([^\s,;\"']+)", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", redacted)
    redacted = re.sub(
        r"(?i)([?&;](?:api[_-]?key|token|key|authorization)=)([^&#\s]+)",
        r"\1[REDACTED]",
        redacted,
    )
    if len(redacted) <= limit:
        return redacted
    return redacted[:limit] + "…[truncated]"


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
