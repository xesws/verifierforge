"""A small tiered verifier for natural-language-to-SQL completions."""

from __future__ import annotations

from collections import Counter
import sqlite3
from typing import Any, Sequence

import sqlparse
from sqlparse import tokens as sql_tokens

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
        if not self._is_single_read_only_statement(parsed):
            return self._with_length_penalty(score, completion)

        try:
            with sqlite3.connect(":memory:") as connection:
                connection.executescript(self.schema_sql)
                connection.set_authorizer(self._read_only_authorizer)
                actual_results = connection.execute(completion).fetchall()
        except (sqlite3.Error, ValueError):
            return self._with_length_penalty(score, completion)

        score = 0.5
        if Counter(actual_results) == Counter(self.expected_results):
            score = 1.0
        return self._with_length_penalty(score, completion)

    @staticmethod
    def _is_single_read_only_statement(parsed: Sequence[sqlparse.sql.Statement]) -> bool:
        """Accept exactly one SQL ``SELECT`` or ``WITH`` statement.

        ``sqlparse`` deliberately does not validate SQL semantics, so this is a
        lexical gate before SQLite sees model-generated text. The SQLite
        authorizer below remains the execution-time backstop.
        """
        if len(parsed) != 1:
            return False

        significant_tokens = [
            token
            for token in parsed[0].flatten()
            if not token.is_whitespace and token.ttype not in sql_tokens.Comment
        ]
        if not significant_tokens:
            return False

        first = significant_tokens[0].normalized.upper()
        if first not in {"SELECT", "WITH"}:
            return False

        forbidden_keywords = {
            "ALTER",
            "ANALYZE",
            "ATTACH",
            "BEGIN",
            "COMMIT",
            "CREATE",
            "DELETE",
            "DETACH",
            "DROP",
            "END",
            "INSERT",
            "PRAGMA",
            "REINDEX",
            "RELEASE",
            "REPLACE",
            "ROLLBACK",
            "SAVEPOINT",
            "UPDATE",
            "VACUUM",
        }
        return not any(
            token.normalized.upper() in forbidden_keywords
            for token in significant_tokens
            if token.ttype not in sql_tokens.Literal.String
        )

    @staticmethod
    def _read_only_authorizer(
        action: int,
        arg1: str | None,
        arg2: str | None,
        database_name: str | None,
        trigger_name: str | None,
    ) -> int:
        """Deny every SQLite action except the read-only actions a query needs."""
        del arg1, database_name, trigger_name

        if action == sqlite3.SQLITE_FUNCTION:
            # SQLite's extension loader is a function call, but not a read-only
            # query operation. Keep it unavailable even if an embedding process
            # has enabled extension loading elsewhere.
            if (arg2 or "").lower() == "load_extension":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        read_only_actions = {
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_RECURSIVE,
            sqlite3.SQLITE_SELECT,
        }
        return sqlite3.SQLITE_OK if action in read_only_actions else sqlite3.SQLITE_DENY

    @staticmethod
    def _with_length_penalty(score: float, completion: str) -> float:
        if len(completion) > 400:
            score -= 0.05
        return max(0.0, min(1.0, score))
