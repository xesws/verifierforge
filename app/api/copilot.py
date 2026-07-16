"""Laptop-local Verifier Copilot routes for reviewable NL-to-SQL proposals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.gpt import (
    LLMClient,
    LLMConfigurationError,
    LLMResponseError,
    LLMSettings,
)
from app.sandbox import DockerSandbox, SandboxResult, SandboxUnavailableError


router = APIRouter(prefix="/copilot/nl2sql", tags=["verifier-copilot"])
_BATCH_SIZE = 10


class NL2SQLExample(BaseModel):
    """A reviewed example supplied as context to the Copilot."""

    prompt: str = Field(min_length=1)
    sql: str = Field(min_length=1)


class ProposalRequest(BaseModel):
    """Inputs needed to draft a reviewable NL-to-SQL verifier proposal."""

    task: str = Field(min_length=1)
    schema_sql: str = Field(min_length=1)
    examples: list[NL2SQLExample] = Field(default_factory=list, max_length=10)
    seed_count: int = Field(default=50, ge=1, le=50)


class ProposedCase(BaseModel):
    """One candidate training case. A human reviews it before it reaches a run."""

    case_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    expected_sql: str = Field(min_length=1)
    expected_results: list[list[Any]] = Field(default_factory=list)


class ProposalResponse(BaseModel):
    """A proposal is descriptive only; it is never written into source automatically."""

    model: str
    verifier_code: str
    test_code: str
    tiers: dict[str, str]
    cases: list[ProposedCase]
    review_required: bool = True


class ValidationRequest(BaseModel):
    """Untrusted standalone Python candidate submitted for Docker validation."""

    candidate_code: str = Field(min_length=1, max_length=100_000)


class ValidationResponse(BaseModel):
    """Bounded sandbox diagnostics suitable for the local UI."""

    passed: bool
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool


class StructuredCompletionClient(Protocol):
    """The small structured-completion surface required by the Copilot."""

    @property
    def model(self) -> str:
        """Configured model to report with a generated proposal."""
        ...

    def complete_json(
        self,
        messages: Sequence[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> dict[str, Any]: ...


class _ProposalBatch(BaseModel):
    """The JSON object requested from one bounded Copilot call."""

    cases: list[ProposedCase]
    verifier_code: str | None = None
    test_code: str | None = None
    tiers: dict[str, str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _lift_misplaced_design_fields(cls, value: Any) -> Any:
        """Tolerate a provider placing first-batch design fields in its first case.

        The prompt requests top-level fields. This narrow repair keeps the API
        reviewable when an otherwise valid structured response nests them one
        level too deeply; it never writes the proposal into source.
        """
        if not isinstance(value, Mapping):
            return value
        normalized = dict(value)
        cases = normalized.get("cases")
        if not isinstance(cases, list):
            return normalized
        for field_name in ("verifier_code", "test_code", "tiers"):
            if normalized.get(field_name):
                continue
            for case in cases:
                if isinstance(case, Mapping) and case.get(field_name):
                    normalized[field_name] = case[field_name]
                    break
        return normalized


class CopilotGenerationError(RuntimeError):
    """Raised when a provider response cannot yield a reviewable proposal."""


class VerifierCopilot:
    """Generate small, batched candidate sets without mutating project files."""

    def __init__(self, client: StructuredCompletionClient) -> None:
        self._client = client

    def propose(self, request: ProposalRequest) -> ProposalResponse:
        """Generate exactly ``seed_count`` unique candidate cases in batches of ten."""
        batches: list[_ProposalBatch] = []
        remaining = request.seed_count
        batch_number = 1
        while remaining:
            count = min(_BATCH_SIZE, remaining)
            batches.append(
                self._generate_batch(
                    request=request,
                    batch_number=batch_number,
                    count=count,
                    include_design=batch_number == 1,
                )
            )
            remaining -= count
            batch_number += 1

        first = batches[0]
        if not first.verifier_code or not first.test_code or not first.tiers:
            raise CopilotGenerationError(
                "The first Copilot batch omitted verifier code, tests, or tiers."
            )

        cases = [case for batch in batches for case in batch.cases]
        if len(cases) != request.seed_count:
            raise CopilotGenerationError(
                f"Copilot returned {len(cases)} cases; expected {request.seed_count}."
            )
        if len({case.case_id for case in cases}) != len(cases):
            raise CopilotGenerationError("Copilot returned duplicate case_id values.")

        return ProposalResponse(
            model=self._client.model,
            verifier_code=first.verifier_code,
            test_code=first.test_code,
            tiers=first.tiers,
            cases=cases,
        )

    def _generate_batch(
        self,
        *,
        request: ProposalRequest,
        batch_number: int,
        count: int,
        include_design: bool,
    ) -> _ProposalBatch:
        """Call once and make one bounded repair request for malformed JSON."""
        last_error: Exception | None = None
        for repair_attempt in range(2):
            try:
                payload = self._client.complete_json(
                    _proposal_messages(
                        request=request,
                        batch_number=batch_number,
                        count=count,
                        include_design=include_design,
                        repair_error=str(last_error) if last_error else None,
                    ),
                    temperature=0.2,
                )
                batch = _ProposalBatch.model_validate(payload)
                if len(batch.cases) != count:
                    raise CopilotGenerationError(
                        f"Batch {batch_number} returned {len(batch.cases)} cases; expected {count}."
                    )
                return batch
            except (LLMResponseError, ValidationError, CopilotGenerationError) as error:
                last_error = error
        raise CopilotGenerationError(
            f"Copilot batch {batch_number} remained invalid after one repair request: {last_error}"
        )


def _proposal_messages(
    *,
    request: ProposalRequest,
    batch_number: int,
    count: int,
    include_design: bool,
    repair_error: str | None,
) -> list[dict[str, str]]:
    """Keep the prompt explicit so a human can reproduce the proposal boundary."""
    example_text = "\n".join(
        f"- prompt: {example.prompt}\n  sql: {example.sql}"
        for example in request.examples
    ) or "(no examples supplied)"
    design_fields = (
        'Also include non-empty "verifier_code", "test_code", and "tiers" fields. '
        'The tiers map must use string score keys such as "0.2" and "1.0". '
        if include_design
        else "Do not repeat verifier_code, test_code, or tiers. "
    )
    repair = (
        f"Your previous response was unusable ({repair_error}). Return a corrected JSON object only. "
        if repair_error
        else ""
    )
    return [
        {
            "role": "system",
            "content": (
                "You are VerifierForge's NL-to-SQL verifier Copilot. Return only a JSON "
                "object; never claim code was executed. Candidate output is for human review."
            ),
        },
        {
            "role": "user",
            "content": (
                f"{repair}Create batch {batch_number} with exactly {count} unique NL-to-SQL "
                "cases for this task. Each case must have only case_id, prompt, expected_sql, and "
                "expected_results (a JSON array of row arrays). "
                f"{design_fields}Task:\n{request.task}\n\nSQLite schema:\n{request.schema_sql}"
                f"\n\nReviewed examples:\n{example_text}"
            ),
        },
    ]


def get_copilot() -> VerifierCopilot:
    """Construct the laptop-only configured LLM client when the route is invoked."""
    try:
        settings = LLMSettings.from_env()
    except LLMConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return VerifierCopilot(LLMClient(settings))


def get_sandbox() -> DockerSandbox:
    """Construct a Docker-only validator; absence is reported by the route."""
    return DockerSandbox()


@router.post("/proposals", response_model=ProposalResponse)
def create_proposals(
    request: ProposalRequest,
    copilot: VerifierCopilot = Depends(get_copilot),
) -> ProposalResponse:
    """Draft a proposal without modifying a verifier, fixture, or run."""
    try:
        return copilot.propose(request)
    except CopilotGenerationError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.post("/validate", response_model=ValidationResponse)
def validate_candidate(
    request: ValidationRequest,
    sandbox: DockerSandbox = Depends(get_sandbox),
) -> ValidationResponse:
    """Run one candidate in the restricted Docker boundary, never on the host."""
    try:
        result: SandboxResult = sandbox.validate(request.candidate_code)
    except SandboxUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return ValidationResponse(
        passed=result.passed,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_seconds=result.duration_seconds,
        timed_out=result.timed_out,
    )
