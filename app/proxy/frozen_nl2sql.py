"""Read-only lookup of the frozen NL2SQL training records used by the guardian."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FROZEN_TRAINING_POOL = REPOSITORY_ROOT / "data" / "nl2sql" / "v0.10.0-training-pool.jsonl"


@dataclass(frozen=True)
class FrozenNL2SQLCase:
    prompt: str
    schema_sql: str
    expected_results: tuple[tuple[Any, ...], ...]
    reference_sql: str


def case_for_prompt(prompt: str, *, pool_path: Path = FROZEN_TRAINING_POOL) -> FrozenNL2SQLCase | None:
    """Return one immutable frozen case, keyed by its full prompt text."""
    return _load_cases(str(Path(pool_path).resolve())).get(prompt)


@lru_cache(maxsize=8)
def _load_cases(path_string: str) -> dict[str, FrozenNL2SQLCase]:
    cases: dict[str, FrozenNL2SQLCase] = {}
    with Path(path_string).open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            raw = json.loads(line)
            case = _parse_case(raw)
            if case is not None:
                cases[case.prompt] = case
    return cases


def _parse_case(raw: object) -> FrozenNL2SQLCase | None:
    if not isinstance(raw, dict):
        return None
    prompt = raw.get("prompt")
    schema_sql = raw.get("schema_sql")
    reference_sql = raw.get("reference_sql")
    expected_results = raw.get("expected_results")
    if not all(isinstance(value, str) and value for value in (prompt, schema_sql, reference_sql)):
        return None
    if not isinstance(expected_results, list) or any(not isinstance(row, list) for row in expected_results):
        return None
    return FrozenNL2SQLCase(
        prompt=prompt,
        schema_sql=schema_sql,
        expected_results=tuple(tuple(row) for row in expected_results),
        reference_sql=reference_sql,
    )
