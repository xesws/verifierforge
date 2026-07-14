"""A small tiered verifier for natural-language-to-SQL completions."""

from __future__ import annotations

from collections import Counter
import sqlite3
from typing import Any, Sequence

import sqlparse

from core.verifier_base import Verifier


class NL2SQLVerifier(Verifier):
    """Score SQL by parseability, execution, and result-set correctness."""

    def __init__(
        self, schema_sql: str, expected_results: Sequence[Sequence[Any]]
    ) -> None:
        self.schema_sql = schema_sql
        self.expected_results = [tuple(row) for row in expected_results]

    @classmethod
    def tiers(cls) -> dict[float, str]:
        return {
            0.2: "SQL parses successfully.",
            0.5: "SQL executes against the provided SQLite schema.",
            1.0: "The resulting rows match the expected result set.",
        }

    def score(self, prompt: str, completion: str) -> float:
        del prompt  # The schema and expected rows define this verifier's reward.

        try:
            parsed = sqlparse.parse(completion)
        except Exception:
            return 0.0

        if not completion.strip() or not parsed:
            return 0.0

        score = 0.2
        try:
            with sqlite3.connect(":memory:") as connection:
                connection.executescript(self.schema_sql)
                actual_results = connection.execute(completion).fetchall()
        except (sqlite3.Error, ValueError):
            return self._with_length_penalty(score, completion)

        score = 0.5
        if Counter(actual_results) == Counter(self.expected_results):
            score = 1.0
        return self._with_length_penalty(score, completion)

    @staticmethod
    def _with_length_penalty(score: float, completion: str) -> float:
        if len(completion) > 400:
            score -= 0.05
        return max(0.0, min(1.0, score))
