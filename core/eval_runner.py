"""Reusable sampling evaluation for verifier-backed candidate records.

The runner deliberately knows only the small completion protocol used by the
application client.  It owns no provider configuration and makes no network
requests itself; callers inject the completion source they want to use.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import math
from typing import Any, Protocol

from core.rewards.nl2sql import NL2SQLVerifier


class EvaluationRecordError(ValueError):
    """Raised when a candidate cannot be safely evaluated."""


class CompletionError(RuntimeError):
    """Raised when an injected completion source cannot produce a string."""


class CompletionClient(Protocol):
    """The provider-neutral protocol required by :func:`evaluate_records`."""

    def complete(
        self,
        messages: Sequence[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        """Return one assistant completion."""


CompletionSource = CompletionClient | Callable[..., str]


@dataclass(frozen=True)
class EvaluationRecord:
    """The verifier inputs for one prompt in a candidate dataset."""

    prompt: str
    schema_sql: str
    expected_results: tuple[tuple[Any, ...], ...]
    record_id: str | None = None


@dataclass(frozen=True)
class SampleGroup:
    """Scores and pass/fail labels for one prompt's ``k`` completions."""

    record_id: str | None
    scores: tuple[float, ...]
    full_passes: tuple[bool, ...]


@dataclass(frozen=True)
class EvaluationMetrics:
    """Aggregate Gate A metrics over independently sampled prompt groups."""

    baseline_pass_at_1: float
    pass_at_k: float
    mixed_fraction: float
    record_count: int
    k: int

    def as_dict(self) -> dict[str, float | int]:
        """Return stable JSON-friendly metric names for programmatic callers."""
        return {
            "baseline_pass_at_1": self.baseline_pass_at_1,
            "pass_at_k": self.pass_at_k,
            "mixed_fraction": self.mixed_fraction,
            "record_count": self.record_count,
            "k": self.k,
        }


@dataclass(frozen=True)
class EvaluationRun:
    """The aggregate metrics plus per-record accounting for audit evidence."""

    metrics: EvaluationMetrics
    groups: tuple[SampleGroup, ...]


def parse_evaluation_record(
    raw_record: Mapping[str, Any], *, source: str = "record"
) -> EvaluationRecord:
    """Validate and normalize a JSON-like NL2SQL evaluation record.

    ``expected_results`` intentionally accepts an empty list: a correct SQL
    query may return no rows.  Every non-empty row must still be sequence-like
    so ``NL2SQLVerifier`` receives the result-set shape it expects.
    """
    if not isinstance(raw_record, Mapping):
        raise EvaluationRecordError(f"{source} must be a JSON object")

    prompt = _required_nonempty_string(raw_record, "prompt", source)
    schema_sql = _required_nonempty_string(raw_record, "schema_sql", source)
    expected_results = raw_record.get("expected_results")
    if not _is_sequence(expected_results):
        raise EvaluationRecordError(f"{source}.expected_results must be a list of rows")

    normalized_rows: list[tuple[Any, ...]] = []
    for row_index, row in enumerate(expected_results):
        if not _is_sequence(row):
            raise EvaluationRecordError(
                f"{source}.expected_results[{row_index}] must be a row sequence"
            )
        for column_index, value in enumerate(row):
            try:
                hash(value)
            except TypeError as error:
                raise EvaluationRecordError(
                    f"{source}.expected_results[{row_index}][{column_index}] "
                    "must be hashable"
                ) from error
        normalized_rows.append(tuple(row))

    record_id = raw_record.get("id")
    if record_id is not None and (
        not isinstance(record_id, str) or not record_id.strip()
    ):
        raise EvaluationRecordError(f"{source}.id must be a non-empty string when set")

    return EvaluationRecord(
        prompt=prompt,
        schema_sql=schema_sql,
        expected_results=tuple(normalized_rows),
        record_id=record_id,
    )


def evaluate_records(
    records: Iterable[EvaluationRecord | Mapping[str, Any]],
    completion_source: CompletionSource,
    *,
    k: int = 8,
    model: str | None = None,
    temperature: float = 1.0,
    workers: int = 1,
) -> EvaluationRun:
    """Sample ``k`` completions per record and score them with NL2SQLVerifier.

    A full pass is deliberately exact: only a verifier score of ``1.0``
    counts.  This prevents a long matching query with the verifier's length
    penalty, or a partial parse/execution score, from being admitted as a pass.
    """
    _validate_sampling_options(k=k, temperature=temperature, workers=workers)
    normalized_records = _normalize_records(records)
    if not normalized_records:
        raise EvaluationRecordError("at least one evaluation record is required")

    samples = _evaluate_samples(
        normalized_records,
        completion_source,
        k=k,
        model=model,
        temperature=temperature,
        workers=workers,
    )
    groups = _group_samples(normalized_records, samples, k=k)

    record_count = len(groups)
    metrics = EvaluationMetrics(
        baseline_pass_at_1=sum(group.full_passes[0] for group in groups) / record_count,
        pass_at_k=sum(any(group.full_passes) for group in groups) / record_count,
        mixed_fraction=sum(
            any(group.full_passes) and not all(group.full_passes) for group in groups
        )
        / record_count,
        record_count=record_count,
        k=k,
    )
    return EvaluationRun(metrics=metrics, groups=tuple(groups))


def _normalize_records(
    records: Iterable[EvaluationRecord | Mapping[str, Any]],
) -> list[EvaluationRecord]:
    if isinstance(records, (str, bytes, bytearray, Mapping)):
        raise EvaluationRecordError("records must be an iterable of record objects")

    normalized: list[EvaluationRecord] = []
    for index, record in enumerate(records, start=1):
        if isinstance(record, EvaluationRecord):
            normalized.append(record)
        else:
            normalized.append(parse_evaluation_record(record, source=f"record {index}"))
    return normalized


def _evaluate_samples(
    records: Sequence[EvaluationRecord],
    completion_source: CompletionSource,
    *,
    k: int,
    model: str | None,
    temperature: float,
    workers: int,
) -> list[tuple[int, int, float]]:
    """Evaluate every record/sample pair, retaining submission order for audit."""
    jobs = [
        (record_index, sample_index, record)
        for record_index, record in enumerate(records, start=1)
        for sample_index in range(1, k + 1)
    ]
    if workers == 1:
        return [
            _score_sample(
                record,
                completion_source,
                model=model,
                temperature=temperature,
                record_index=record_index,
                sample_index=sample_index,
            )
            for record_index, sample_index, record in jobs
        ]

    # Calls are I/O-bound.  Future results are collected in submission order,
    # so concurrent request completion never changes pass@1 or audit ordering.
    with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as executor:
        futures = [
            executor.submit(
                _score_sample,
                record,
                completion_source,
                model=model,
                temperature=temperature,
                record_index=record_index,
                sample_index=sample_index,
            )
            for record_index, sample_index, record in jobs
        ]
        try:
            return [future.result() for future in futures]
        except BaseException:
            for future in futures:
                future.cancel()
            raise


def _score_sample(
    record: EvaluationRecord,
    completion_source: CompletionSource,
    *,
    model: str | None,
    temperature: float,
    record_index: int,
    sample_index: int,
) -> tuple[int, int, float]:
    verifier = NL2SQLVerifier(record.schema_sql, record.expected_results)
    completion = _complete(
        completion_source,
        messages=[{"role": "user", "content": record.prompt}],
        model=model,
        temperature=temperature,
        record_index=record_index,
        sample_index=sample_index,
    )
    return record_index, sample_index, verifier.score(record.prompt, completion)


def _group_samples(
    records: Sequence[EvaluationRecord],
    samples: Sequence[tuple[int, int, float]],
    *,
    k: int,
) -> list[SampleGroup]:
    scores_by_record = [[0.0] * k for _ in records]
    for record_index, sample_index, score in samples:
        scores_by_record[record_index - 1][sample_index - 1] = score
    return [
        SampleGroup(
            record_id=record.record_id,
            scores=tuple(scores),
            full_passes=tuple(score == 1.0 for score in scores),
        )
        for record, scores in zip(records, scores_by_record, strict=True)
    ]


def _validate_sampling_options(*, k: int, temperature: float, workers: int) -> None:
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise ValueError("temperature must be a finite non-negative number")
    if not math.isfinite(temperature) or temperature < 0:
        raise ValueError("temperature must be a finite non-negative number")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")


def _complete(
    completion_source: CompletionSource,
    *,
    messages: list[dict[str, str]],
    model: str | None,
    temperature: float,
    record_index: int,
    sample_index: int,
) -> str:
    complete = getattr(completion_source, "complete", completion_source)
    if not callable(complete):
        raise CompletionError("completion source must be callable or define complete()")

    try:
        completion = complete(messages, model=model, temperature=temperature)
    except Exception as error:
        raise CompletionError(
            f"completion failed for record {record_index}, sample {sample_index}"
        ) from error

    if not isinstance(completion, str):
        raise CompletionError(
            f"completion source returned a non-string for record {record_index}, "
            f"sample {sample_index}"
        )
    return completion


def _required_nonempty_string(
    record: Mapping[str, Any], field: str, source: str
) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise EvaluationRecordError(f"{source}.{field} must be a non-empty string")
    return value


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )
