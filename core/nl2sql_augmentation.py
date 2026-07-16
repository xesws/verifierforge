"""Verifier-screened NL-to-SQL augmentation helpers for the D3 data freeze."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Protocol

from core.rewards.nl2sql import NL2SQLVerifier


_QUESTION_MARKER = "\n\nQuestion: "
_SQL_MARKER = "\nSQL:"
_INSERT_INTO_PATTERN = re.compile(r"\bINSERT\s+INTO\b", re.IGNORECASE)


class AugmentationInputError(ValueError):
    """Raised when a seed JSONL file cannot be used safely for augmentation."""


class JSONCompletionClient(Protocol):
    """The deliberately small structured-completion surface the engine needs."""

    def complete_json(
        self,
        messages: Sequence[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class SeedCase:
    """The normalized, local-only information needed to expand one seed."""

    seed_id: str
    prompt: str
    schema_sql: str
    expected_results: list[list[Any]]
    question: str | None = None
    reference_sql: str | None = None
    question_prompt_prefix: str | None = None
    question_prompt_suffix: str | None = None


@dataclass(frozen=True)
class AugmentationSummary:
    """Count-only evidence from an augmentation run, safe to persist or print."""

    seed_count: int
    variants_per_seed: int
    proposed_count: int
    accepted_count: int
    rejected_shape_count: int
    rejected_expected_results_count: int
    rejected_verifier_count: int
    duplicate_count: int
    discarded_excess_count: int
    malformed_response_count: int

    def as_dict(self) -> dict[str, int]:
        """Return stable JSON-safe counters without provider payloads or secrets."""
        return {
            "seed_count": self.seed_count,
            "variants_per_seed": self.variants_per_seed,
            "proposed_count": self.proposed_count,
            "accepted_count": self.accepted_count,
            "rejected_shape_count": self.rejected_shape_count,
            "rejected_expected_results_count": self.rejected_expected_results_count,
            "rejected_verifier_count": self.rejected_verifier_count,
            "duplicate_count": self.duplicate_count,
            "discarded_excess_count": self.discarded_excess_count,
            "malformed_response_count": self.malformed_response_count,
        }


@dataclass(frozen=True)
class _CandidateVariant:
    """A shape-validated provider candidate before verifier screening."""

    question: str
    prompt: str | None
    reference_sql: str
    expected_results: list[list[Any]]


def load_seed_cases(path: Path) -> list[SeedCase]:
    """Load either the reviewed V1 fixture or a compatible prompt JSONL file.

    The checked-in V1 seed file stores a human-readable ``question`` and relies
    on its fixture helper to attach the execution schema and full prompt. A
    compatible external file instead supplies ``id``, ``prompt``,
    ``schema_sql``, and ``expected_results`` directly. Source SQL is optional
    for compatible files because generated SQL is independently verifier-gated.
    """
    raw_rows = _read_jsonl(path)
    if raw_rows and all(
        isinstance(row, Mapping) and "question" in row and "prompt" not in row
        for row in raw_rows
    ):
        # Reuse the reviewed fixture's schema/prompt construction rather than
        # copying an execution fixture into this augmentation module.
        from trainer.data.nl2sql_v1 import load_cases

        return [_normalise_seed(case, path, line_number) for line_number, case in enumerate(load_cases(path), start=1)]

    return [
        _normalise_seed(row, path, line_number)
        for line_number, row in enumerate(raw_rows, start=1)
    ]


def augmentation_messages(seed: SeedCase, variants_per_seed: int) -> list[dict[str, str]]:
    """Build one bounded JSON request without embedding client configuration."""
    source_sql = (
        f"\n\nCanonical SQL correctness anchor (never put this in the prompt):\n"
        f"{seed.reference_sql}"
        if seed.reference_sql
        else ""
    )
    expected_rows = _canonical_json(seed.expected_results)
    if seed.question_prompt_prefix is not None:
        variant_shape = (
            '{"variants":[{"question":"...","reference_sql":"...",'
            '"expected_results":[["..."]]}]}'
        )
        prompt_rule = (
            "Do not return a `prompt`: the host will place your question into the "
            "reviewed DDL-only seed prompt template."
        )
    else:
        variant_shape = (
            '{"variants":[{"question":"...","prompt":"...",'
            '"reference_sql":"...","expected_results":[["..."]]}]}'
        )
        prompt_rule = (
            "`prompt` is the complete standalone model prompt for that question, including "
            "the necessary schema and read-only-SQL instruction, but no answer SQL and no "
            "INSERT INTO fixture data."
        )
    return [
        {
            "role": "system",
            "content": (
                "You create verifier-screened NL-to-SQL training data. Return one JSON "
                "object only, with no markdown or explanation."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Create at most {variants_per_seed} semantic variants of the seed task. "
                "Every variant must use this exact JSON shape: "
                f"{variant_shape}. `question` is a plain-language paraphrase. {prompt_rule} "
                "`reference_sql` is exactly one read-only SQLite SELECT or WITH statement. "
                "Copy `expected_results` exactly as supplied; do not infer or change its "
                "values.\n\n"
                f"Seed prompt:\n{seed.prompt}\n\n"
                f"Expected result rows:\n{expected_rows}"
                f"{source_sql}"
            ),
        },
    ]


def augment_seed_cases(
    *,
    seeds: Sequence[SeedCase],
    client: JSONCompletionClient,
    variants_per_seed: int = 8,
    model: str | None = None,
) -> tuple[list[dict[str, Any]], AugmentationSummary]:
    """Generate, validate, deduplicate, and verifier-screen candidate records.

    Provider responses are intentionally held only long enough to extract a
    candidate. The returned records contain host-selected fields only; no raw
    response, model metadata, API key, or provider provenance is retained.
    """
    if variants_per_seed < 1:
        raise ValueError("variants_per_seed must be at least 1")

    ordered_seeds = sorted(seeds, key=lambda seed: seed.seed_id)
    if len({seed.seed_id for seed in ordered_seeds}) != len(ordered_seeds):
        raise AugmentationInputError("seed ids must be unique")
    if any(_contains_insert_into(seed.prompt) for seed in ordered_seeds):
        raise AugmentationInputError("seed prompts must not include INSERT INTO fixture data")

    candidates: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    proposed_count = 0
    rejected_shape_count = 0
    rejected_expected_results_count = 0
    rejected_verifier_count = 0
    duplicate_count = 0
    discarded_excess_count = 0
    malformed_response_count = 0

    for seed in ordered_seeds:
        try:
            response = client.complete_json(
                augmentation_messages(seed, variants_per_seed),
                model=model,
                temperature=0.4,
            )
        except ValueError:
            # A malformed structured response is one failed candidate batch,
            # not grounds to publish unverified data or discard every other
            # seed. Transport/authentication errors remain RuntimeErrors and
            # intentionally fail the whole command without partial output.
            malformed_response_count += 1
            rejected_shape_count += 1
            continue
        raw_variants = _response_variants(response)
        if raw_variants is None:
            malformed_response_count += 1
            rejected_shape_count += 1
            continue

        if len(raw_variants) > variants_per_seed:
            discarded_excess_count += len(raw_variants) - variants_per_seed
            raw_variants = raw_variants[:variants_per_seed]

        verifier = NL2SQLVerifier(seed.schema_sql, seed.expected_results)
        accepted_for_seed = 0
        for raw_variant in raw_variants:
            proposed_count += 1
            candidate = _normalise_candidate(
                raw_variant, prompt_required=not _has_question_template(seed)
            )
            if candidate is None:
                rejected_shape_count += 1
                continue
            if not _json_values_match(candidate.expected_results, seed.expected_results):
                rejected_expected_results_count += 1
                continue
            prompt = _render_training_prompt(seed, candidate)
            if prompt is None or _contains_insert_into(prompt):
                rejected_shape_count += 1
                continue
            if verifier.score(prompt, candidate.reference_sql) != 1.0:
                rejected_verifier_count += 1
                continue

            pair = (prompt, candidate.reference_sql)
            if pair in seen_pairs:
                duplicate_count += 1
                continue
            seen_pairs.add(pair)

            accepted_for_seed += 1
            candidates.append(
                {
                    "id": f"aug-{seed.seed_id}-{accepted_for_seed:03d}",
                    "seed_id": seed.seed_id,
                    "question": candidate.question,
                    "prompt": prompt,
                    "schema_sql": seed.schema_sql,
                    "expected_results": seed.expected_results,
                    "reference_sql": candidate.reference_sql,
                }
            )

    return candidates, AugmentationSummary(
        seed_count=len(ordered_seeds),
        variants_per_seed=variants_per_seed,
        proposed_count=proposed_count,
        accepted_count=len(candidates),
        rejected_shape_count=rejected_shape_count,
        rejected_expected_results_count=rejected_expected_results_count,
        rejected_verifier_count=rejected_verifier_count,
        duplicate_count=duplicate_count,
        discarded_excess_count=discarded_excess_count,
        malformed_response_count=malformed_response_count,
    )


def write_candidates_jsonl_atomic(
    output_path: Path, candidates: Sequence[Mapping[str, Any]]
) -> Path:
    """Atomically replace ``output_path`` with deterministic JSONL candidate rows."""
    payload = "".join(f"{_canonical_json(dict(candidate))}\n" for candidate in candidates)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(payload)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, output_path)
        return output_path
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _read_jsonl(path: Path) -> list[Mapping[str, Any]]:
    if not path.is_file():
        raise AugmentationInputError(f"seed JSONL does not exist: {path}")

    rows: list[Mapping[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise AugmentationInputError(
                    f"invalid JSON in {path} line {line_number}"
                ) from error
            if not isinstance(row, Mapping):
                raise AugmentationInputError(
                    f"seed JSONL line {line_number} must be an object"
                )
            rows.append(row)
    if not rows:
        raise AugmentationInputError(f"seed JSONL is empty: {path}")
    return rows


def _normalise_seed(
    raw: Mapping[str, Any], path: Path, line_number: int
) -> SeedCase:
    seed_id = raw.get("id") or raw.get("seed_id")
    prompt = raw.get("prompt")
    schema_sql = raw.get("schema_sql")
    expected_results = raw.get("expected_results")
    question = raw.get("question")
    reference_sql = raw.get("reference_sql")

    if not _nonempty_text(seed_id):
        raise AugmentationInputError(f"{path} line {line_number} has an invalid id")
    if not _nonempty_text(prompt):
        raise AugmentationInputError(f"{path} line {line_number} has an invalid prompt")
    if _contains_insert_into(prompt):
        raise AugmentationInputError(
            f"{path} line {line_number} prompt must not include INSERT INTO fixture data"
        )
    if not _nonempty_text(schema_sql):
        raise AugmentationInputError(f"{path} line {line_number} has an invalid schema_sql")
    if not _is_result_rows(expected_results):
        raise AugmentationInputError(
            f"{path} line {line_number} expected_results must be an array of row arrays"
        )
    _validate_json_value(expected_results, path, line_number, "expected_results")
    if question is not None and not _nonempty_text(question):
        raise AugmentationInputError(f"{path} line {line_number} has an invalid question")
    if reference_sql is not None and not _nonempty_text(reference_sql):
        raise AugmentationInputError(
            f"{path} line {line_number} has an invalid reference_sql"
        )

    question_prompt_prefix, question_prompt_suffix = _question_prompt_parts(prompt)
    return SeedCase(
        seed_id=seed_id.strip(),
        prompt=prompt.strip(),
        schema_sql=schema_sql.strip(),
        expected_results=expected_results,
        question=question.strip() if isinstance(question, str) else None,
        reference_sql=reference_sql.strip() if isinstance(reference_sql, str) else None,
        question_prompt_prefix=question_prompt_prefix,
        question_prompt_suffix=question_prompt_suffix,
    )


def _response_variants(response: Mapping[str, Any]) -> list[Any] | None:
    variants = response.get("variants") if isinstance(response, Mapping) else None
    return variants if isinstance(variants, list) else None


def _normalise_candidate(
    raw: Any, *, prompt_required: bool
) -> _CandidateVariant | None:
    if not isinstance(raw, Mapping):
        return None
    question = raw.get("question")
    prompt = raw.get("prompt")
    expected_results = raw.get("expected_results")
    reference_sql = raw.get("reference_sql")
    alternate_sql = raw.get("sql")

    if reference_sql is None:
        reference_sql = alternate_sql
    elif alternate_sql is not None and alternate_sql != reference_sql:
        return None
    if not (
        _nonempty_text(question)
        and _nonempty_text(reference_sql)
        and _is_result_rows(expected_results)
    ):
        return None
    if prompt_required and not _nonempty_text(prompt):
        return None
    if prompt is not None and not _nonempty_text(prompt):
        return None
    try:
        _canonical_json(expected_results)
    except (TypeError, ValueError):
        return None
    return _CandidateVariant(
        question=question.strip(),
        prompt=prompt.strip() if isinstance(prompt, str) else None,
        reference_sql=reference_sql.strip(),
        expected_results=expected_results,
    )


def _has_question_template(seed: SeedCase) -> bool:
    return seed.question_prompt_prefix is not None and seed.question_prompt_suffix is not None


def _render_training_prompt(
    seed: SeedCase, candidate: _CandidateVariant
) -> str | None:
    if _has_question_template(seed):
        assert seed.question_prompt_prefix is not None
        assert seed.question_prompt_suffix is not None
        return f"{seed.question_prompt_prefix}{candidate.question}{seed.question_prompt_suffix}"
    return candidate.prompt


def _question_prompt_parts(prompt: str) -> tuple[str | None, str | None]:
    """Return the stable V1 prompt shell when its Question/SQL slots are clear."""
    question_start = prompt.rfind(_QUESTION_MARKER)
    if question_start < 0:
        return None, None
    question_end = prompt.find(_SQL_MARKER, question_start + len(_QUESTION_MARKER))
    if question_end < 0 or prompt[question_end + len(_SQL_MARKER) :].strip():
        return None, None
    if not prompt[question_start + len(_QUESTION_MARKER) : question_end].strip():
        return None, None
    return prompt[: question_start + len(_QUESTION_MARKER)], prompt[question_end:]


def _contains_insert_into(prompt: str) -> bool:
    return bool(_INSERT_INTO_PATTERN.search(prompt))


def _is_result_rows(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(row, list) for row in value)


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _json_values_match(left: Any, right: Any) -> bool:
    try:
        return _canonical_json(left) == _canonical_json(right)
    except (TypeError, ValueError):
        return False


def _validate_json_value(value: Any, path: Path, line_number: int, field: str) -> None:
    try:
        _canonical_json(value)
    except (TypeError, ValueError) as error:
        raise AugmentationInputError(
            f"{path} line {line_number} has non-JSON {field}"
        ) from error


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
