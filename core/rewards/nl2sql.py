"""A small tiered verifier for natural-language-to-SQL completions."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import sqlite3
from typing import Any, Sequence

import sqlparse
from sqlparse import tokens as sql_tokens

from core.verifier_base import Verifier


@dataclass(frozen=True)
class NL2SQLScoreBreakdown:
    """The unchanged NL2SQL score plus stage facts needed for audit evidence."""

    parse_score: float
    execution_score: float
    result_match_score: float
    final_score: float
    parser_succeeded: bool
    read_only_statement: bool
    execution_succeeded: bool
    result_matched: bool
    length_penalty: float
    failure_class: str
    failure_detail: str | None

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-ready, sample-evidence representation."""
        return {
            "tier_scores": {
                "parse": self.parse_score,
                "execution": self.execution_score,
                "result_match": self.result_match_score,
            },
            "stages": {
                "parser_succeeded": self.parser_succeeded,
                "read_only_statement": self.read_only_statement,
                "execution_succeeded": self.execution_succeeded,
                "result_matched": self.result_matched,
                "length_penalty": self.length_penalty,
            },
            "final_score": self.final_score,
            "failure_class": self.failure_class,
            "failure_detail": self.failure_detail,
        }


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
        """Return the legacy scalar score without changing its semantics."""
        return self.score_breakdown(prompt, completion).final_score

    def score_breakdown(self, prompt: str, completion: str) -> NL2SQLScoreBreakdown:
        """Return all existing tier outcomes alongside the exact final score."""
        del prompt  # The schema and expected rows define this verifier's reward.

        try:
            parsed = sqlparse.parse(completion)
        except Exception:
            return self._breakdown(
                parse_score=0.0,
                execution_score=0.0,
                result_match_score=0.0,
                completion=completion,
                parser_succeeded=False,
                read_only_statement=False,
                execution_succeeded=False,
                result_matched=False,
                failure_class="parse_failure",
                failure_detail="sqlparse_exception",
            )

        if not completion.strip() or not parsed:
            return self._breakdown(
                parse_score=0.0,
                execution_score=0.0,
                result_match_score=0.0,
                completion=completion,
                parser_succeeded=False,
                read_only_statement=False,
                execution_succeeded=False,
                result_matched=False,
                failure_class="parse_failure",
                failure_detail="empty_or_no_statement",
            )

        parse_score = 0.2
        read_only_statement = self._is_single_read_only_statement(parsed)
        if not read_only_statement:
            return self._breakdown(
                parse_score=parse_score,
                execution_score=0.0,
                result_match_score=0.0,
                completion=completion,
                parser_succeeded=True,
                read_only_statement=False,
                execution_succeeded=False,
                result_matched=False,
                failure_class="execution_error",
                failure_detail="not_single_read_only_statement",
            )

        try:
            with sqlite3.connect(":memory:") as connection:
                connection.executescript(self.schema_sql)
                connection.set_authorizer(self._read_only_authorizer)
                actual_results = connection.execute(completion).fetchall()
        except (sqlite3.Error, ValueError):
            return self._breakdown(
                parse_score=parse_score,
                execution_score=0.0,
                result_match_score=0.0,
                completion=completion,
                parser_succeeded=True,
                read_only_statement=True,
                execution_succeeded=False,
                result_matched=False,
                failure_class="execution_error",
                failure_detail="sqlite_execution_error",
            )

        result_matched = Counter(actual_results) == Counter(self.expected_results)
        if not result_matched:
            return self._breakdown(
                parse_score=parse_score,
                execution_score=0.5,
                result_match_score=0.0,
                completion=completion,
                parser_succeeded=True,
                read_only_statement=True,
                execution_succeeded=True,
                result_matched=False,
                failure_class="executable_not_full_pass",
                failure_detail="result_mismatch",
            )
        return self._breakdown(
            parse_score=parse_score,
            execution_score=0.5,
            result_match_score=1.0,
            completion=completion,
            parser_succeeded=True,
            read_only_statement=True,
            execution_succeeded=True,
            result_matched=True,
            failure_class="full_pass",
            failure_detail=None,
        )

    def _breakdown(
        self,
        *,
        parse_score: float,
        execution_score: float,
        result_match_score: float,
        completion: str,
        parser_succeeded: bool,
        read_only_statement: bool,
        execution_succeeded: bool,
        result_matched: bool,
        failure_class: str,
        failure_detail: str | None,
    ) -> NL2SQLScoreBreakdown:
        base_score = max(parse_score, execution_score, result_match_score)
        final_score = self._with_length_penalty(base_score, completion)
        length_penalty = base_score - final_score
        if result_matched and final_score < 1.0:
            failure_class = "executable_not_full_pass"
            failure_detail = "length_penalized_exact_result"
        return NL2SQLScoreBreakdown(
            parse_score=parse_score,
            execution_score=execution_score,
            result_match_score=result_match_score,
            final_score=final_score,
            parser_succeeded=parser_succeeded,
            read_only_statement=read_only_statement,
            execution_succeeded=execution_succeeded,
            result_matched=result_matched,
            length_penalty=length_penalty,
            failure_class=failure_class,
            failure_detail=failure_detail,
        )

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
