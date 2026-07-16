"""Reusable sampling evaluation for verifier-backed candidate records.

The runner deliberately knows only the small completion protocol used by the
application client.  It owns no provider configuration and makes no network
requests itself; callers inject the completion source they want to use.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
import math
import re
from typing import Any, Protocol

from core.rewards.nl2sql import NL2SQLVerifier


class EvaluationRecordError(ValueError):
    """Raised when a candidate cannot be safely evaluated."""


MAX_IN_FLIGHT = 8
MAX_COMPLETION_ATTEMPTS = 2


@dataclass(frozen=True)
class CompletionAttemptFailure:
    """One redacted provider attempt belonging to a logical sample request."""

    attempt: int
    exception_type: str
    message: str
    status_code: int | None
    provider_body: str | None


class CompletionError(RuntimeError):
    """A terminal logical-sample failure with its original safe audit facts."""

    def __init__(
        self,
        message: str,
        *,
        request_ordinal: int | None = None,
        record_index: int | None = None,
        sample_index: int | None = None,
        attempts: Sequence[CompletionAttemptFailure] = (),
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.request_ordinal = request_ordinal
        self.record_index = record_index
        self.sample_index = sample_index
        self.attempts = tuple(attempts)
        self.cause = cause


class EvaluationCompletionError(RuntimeError):
    """Raised when any Gate A sample terminates without a usable completion."""

    def __init__(
        self,
        failures: Sequence[CompletionError],
        *,
        circuit_open: bool,
        completed_logical_samples: int,
        total_logical_samples: int,
        maximum_consecutive_terminal_failures: int,
    ) -> None:
        super().__init__("evaluation has terminal completion failures")
        self.failures = tuple(failures)
        self.circuit_open = circuit_open
        self.completed_logical_samples = completed_logical_samples
        self.total_logical_samples = total_logical_samples
        self.maximum_consecutive_terminal_failures = (
            maximum_consecutive_terminal_failures
        )


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
        metrics: dict[str, float | int] = {
            "baseline_pass_at_1": self.baseline_pass_at_1,
            "pass_at_k": self.pass_at_k,
            "mixed_fraction": self.mixed_fraction,
            "record_count": self.record_count,
            "k": self.k,
        }
        if self.k == 8:
            # Keep the generic pass_at_k/k representation and expose the
            # Gate A fixed-k value as an additive convenience field.
            metrics["pass_at_8"] = self.pass_at_k
        return metrics


@dataclass(frozen=True)
class EvaluationRun:
    """The aggregate metrics plus per-record accounting for audit evidence."""

    metrics: EvaluationMetrics
    groups: tuple[SampleGroup, ...]


@dataclass(frozen=True)
class _SampleJob:
    """A fixed request ordinal, so concurrent completion cannot alter evidence."""

    request_ordinal: int
    record_index: int
    sample_index: int
    record: EvaluationRecord


@dataclass(frozen=True)
class _SampleResult:
    """One successfully completed and verifier-scored logical sample."""

    request_ordinal: int
    record_index: int
    sample_index: int
    score: float


def parse_evaluation_record(
    raw_record: Mapping[str, Any], *, source: str = "record"
) -> EvaluationRecord:
    """Validate and normalize a JSON-like NL2SQL evaluation record.

    ``expected_results`` intentionally accepts an empty list: a correct SQL
    query may return no rows. Every non-empty row must still be sequence-like
    and contain only finite JSON SQL scalars, so ``NL2SQLVerifier`` receives a
    result-set shape whose cells are safe to compare and hash.
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
            if not _is_sql_scalar_json_value(value):
                raise EvaluationRecordError(
                    f"{source}.expected_results[{row_index}][{column_index}] "
                    "must be a finite SQL scalar JSON value "
                    "(string, number, boolean, or null)"
                )
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
) -> list[_SampleResult]:
    """Run samples with bounded concurrency and deterministic failure ordering.

    The scheduler submits no more than eight logical samples at once. Results
    are committed by the ordinal assigned before submission, rather than by
    future-completion order; this makes the ten-consecutive-failure circuit
    breaker reproducible even when network timings differ.
    """
    jobs = [
        _SampleJob(
            request_ordinal=ordinal,
            record_index=record_index,
            sample_index=sample_index,
            record=record,
        )
        for ordinal, (record_index, sample_index, record) in enumerate(
            (
                (record_index, sample_index, record)
                for record_index, record in enumerate(records, start=1)
                for sample_index in range(1, k + 1)
            ),
            start=1,
        )
    ]
    worker_count = min(MAX_IN_FLIGHT, workers, len(jobs))
    results: list[_SampleResult] = []
    failures: list[CompletionError] = []
    buffered: dict[int, _SampleResult | CompletionError] = {}
    next_job_index = 0
    next_commit_ordinal = 1
    consecutive_terminal_failures = 0
    maximum_consecutive_terminal_failures = 0
    completed_logical_samples = 0
    circuit_open = False

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        in_flight: dict[Future[_SampleResult], _SampleJob] = {}

        def submit_until_full() -> None:
            nonlocal next_job_index
            while (
                not circuit_open
                and len(in_flight) < worker_count
                and next_job_index < len(jobs)
                # Never let out-of-order fast futures move the submission
                # window beyond the earliest uncommitted ordinal. This caps
                # post-circuit work at the seven samples already in flight.
                and jobs[next_job_index].request_ordinal
                < next_commit_ordinal + worker_count
            ):
                job = jobs[next_job_index]
                next_job_index += 1
                in_flight[
                    executor.submit(
                        _score_sample,
                        job,
                        completion_source,
                        model=model,
                        temperature=temperature,
                    )
                ] = job

        submit_until_full()
        while in_flight:
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in done:
                job = in_flight.pop(future)
                try:
                    buffered[job.request_ordinal] = future.result()
                except CompletionError as error:
                    buffered[job.request_ordinal] = error
                except Exception as error:
                    buffered[job.request_ordinal] = _unexpected_completion_error(
                        job, error
                    )

            while next_commit_ordinal in buffered:
                outcome = buffered.pop(next_commit_ordinal)
                completed_logical_samples += 1
                if isinstance(outcome, CompletionError):
                    failures.append(outcome)
                    consecutive_terminal_failures += 1
                    maximum_consecutive_terminal_failures = max(
                        maximum_consecutive_terminal_failures,
                        consecutive_terminal_failures,
                    )
                    if consecutive_terminal_failures >= 10:
                        circuit_open = True
                else:
                    results.append(outcome)
                    consecutive_terminal_failures = 0
                next_commit_ordinal += 1

            # A circuit-open run drains only samples already running. No jobs
            # have been queued beyond ``in_flight``, so none start afterward.
            submit_until_full()

    if failures:
        raise EvaluationCompletionError(
            failures,
            circuit_open=circuit_open,
            completed_logical_samples=completed_logical_samples,
            total_logical_samples=len(jobs),
            maximum_consecutive_terminal_failures=maximum_consecutive_terminal_failures,
        )
    return results


def _score_sample(
    job: _SampleJob,
    completion_source: CompletionSource,
    *,
    model: str | None,
    temperature: float,
) -> _SampleResult:
    """Complete and score one pre-ordered logical sample."""
    verifier = NL2SQLVerifier(job.record.schema_sql, job.record.expected_results)
    completion = _complete(
        completion_source,
        messages=[{"role": "user", "content": job.record.prompt}],
        model=model,
        temperature=temperature,
        request_ordinal=job.request_ordinal,
        record_index=job.record_index,
        sample_index=job.sample_index,
    )
    return _SampleResult(
        request_ordinal=job.request_ordinal,
        record_index=job.record_index,
        sample_index=job.sample_index,
        score=verifier.score(job.record.prompt, completion),
    )


def _unexpected_completion_error(job: _SampleJob, error: Exception) -> CompletionError:
    """Turn an unexpected worker exception into the same auditable failure shape."""
    failure = _attempt_failure(0, error)
    completion_error = CompletionError(
        f"completion failed for request {job.request_ordinal}",
        request_ordinal=job.request_ordinal,
        record_index=job.record_index,
        sample_index=job.sample_index,
        attempts=(failure,),
        cause=error,
    )
    completion_error.__cause__ = error
    return completion_error


def _group_samples(
    records: Sequence[EvaluationRecord],
    samples: Sequence[_SampleResult],
    *,
    k: int,
) -> list[SampleGroup]:
    scores_by_record = [[0.0] * k for _ in records]
    for sample in samples:
        scores_by_record[sample.record_index - 1][sample.sample_index - 1] = sample.score
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
    request_ordinal: int,
    record_index: int,
    sample_index: int,
) -> str:
    """Call a completion source twice at most, retaining the original cause."""
    complete = getattr(completion_source, "complete", completion_source)
    if not callable(complete):
        source_error = TypeError("completion source must be callable or define complete()")
        completion_error = CompletionError(
            f"completion failed for request {request_ordinal}",
            request_ordinal=request_ordinal,
            record_index=record_index,
            sample_index=sample_index,
            attempts=(_attempt_failure(0, source_error),),
            cause=source_error,
        )
        raise completion_error from source_error

    attempts: list[CompletionAttemptFailure] = []
    last_error: Exception | None = None
    for attempt in range(1, MAX_COMPLETION_ATTEMPTS + 1):
        try:
            completion = complete(messages, model=model, temperature=temperature)
            if not isinstance(completion, str):
                raise TypeError("completion source returned a non-string")
            return completion
        except Exception as error:
            attempts.append(_attempt_failure(attempt, error))
            last_error = error

    assert last_error is not None
    completion_error = CompletionError(
        f"completion failed for request {request_ordinal}",
        request_ordinal=request_ordinal,
        record_index=record_index,
        sample_index=sample_index,
        attempts=attempts,
        cause=last_error,
    )
    raise completion_error from last_error


def _attempt_failure(attempt: int, error: BaseException) -> CompletionAttemptFailure:
    """Extract redacted status/body facts without depending on a provider SDK."""
    return CompletionAttemptFailure(
        attempt=attempt,
        exception_type=type(error).__name__,
        message=_redact_and_truncate(str(error), limit=1024),
        status_code=_status_code(error),
        provider_body=_provider_body(error),
    )


def _status_code(error: BaseException) -> int | None:
    value = getattr(error, "status_code", None)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _provider_body(error: BaseException) -> str | None:
    value = getattr(error, "provider_body", None)
    if value is None:
        value = getattr(error, "body", None)
    if value is None:
        response = getattr(error, "response", None)
        value = getattr(response, "text", None)
    if value is None:
        return None
    return _redact_and_truncate(str(value))


def _redact_and_truncate(value: str, *, limit: int = 4096) -> str:
    """Redact likely credentials in provider diagnostics before evidence storage."""
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


def _is_sql_scalar_json_value(value: Any) -> bool:
    """Return whether ``value`` is a deterministic SQL-comparable JSON scalar.

    ``NL2SQLVerifier`` compares rows with ``Counter``. Exact built-in scalar
    types keep equality and hashing free from user-defined implementations;
    non-finite floats are excluded because ``NaN`` is not equal to itself.
    ``bool`` is retained because it is a JSON scalar and SQLite represents
    boolean literals as integer-like values.
    """
    value_type = type(value)
    if value_type in (str, int, bool, type(None)):
        return True
    return value_type is float and math.isfinite(value)
