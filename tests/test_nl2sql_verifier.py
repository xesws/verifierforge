import pytest

from core.rewards.nl2sql import NL2SQLVerifier


SCHEMA = """
CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT, team TEXT);
INSERT INTO employees (name, team) VALUES ('Ada', 'research');
INSERT INTO employees (name, team) VALUES ('Grace', 'platform');
"""


def test_nl2sql_scores_parse_only_completion() -> None:
    verifier = NL2SQLVerifier(SCHEMA, [("Ada",)])

    assert verifier.score("find Ada", "SELECT missing FROM employees") == 0.2


def test_nl2sql_scores_executable_but_wrong_completion() -> None:
    verifier = NL2SQLVerifier(SCHEMA, [("Ada",)])

    assert verifier.score("find Ada", "SELECT name FROM employees WHERE id = 2") == 0.5


def test_nl2sql_scores_matching_result_set() -> None:
    verifier = NL2SQLVerifier(SCHEMA, [("Ada",)])

    assert verifier.score("find Ada", "SELECT name FROM employees WHERE id = 1") == 1.0


@pytest.mark.parametrize(
    "completion",
    [
        "INSERT INTO employees (name, team) VALUES ('Linus', 'research')",
        "UPDATE employees SET team = 'platform' WHERE id = 1",
        "DELETE FROM employees WHERE id = 1",
        "PRAGMA table_info(employees)",
        "ATTACH DATABASE 'scratch.db' AS scratch",
        (
            "WITH removed AS (DELETE FROM employees RETURNING name) "
            "SELECT name FROM removed"
        ),
        "SELECT name FROM employees WHERE id = 1; DELETE FROM employees",
    ],
)
def test_nl2sql_unsafe_sql_gets_parse_only_credit(completion: str) -> None:
    verifier = NL2SQLVerifier(SCHEMA, [("Ada",)])

    assert verifier.score("find Ada", completion) == 0.2


def test_nl2sql_allows_one_read_only_with_statement() -> None:
    verifier = NL2SQLVerifier(SCHEMA, [("Ada",)])

    completion = (
        "WITH named_employees AS (SELECT name FROM employees) "
        "SELECT name FROM named_employees WHERE name = 'Ada'"
    )

    assert verifier.score("find Ada", completion) == 1.0


def test_nl2sql_does_not_treat_a_sql_keyword_in_a_string_as_a_write() -> None:
    verifier = NL2SQLVerifier(SCHEMA, [("DELETE",)])

    assert verifier.score("return the word", "SELECT 'DELETE'") == 1.0


def test_nl2sql_v2_extracts_fenced_sql_before_the_unchanged_tier_scorer() -> None:
    verifier = NL2SQLVerifier(SCHEMA, [("Ada",)])
    completion = "```sql\nSELECT name FROM employees WHERE id = 1;\n```"

    breakdown = verifier.score_breakdown("find Ada", completion)

    assert verifier.score("find Ada", completion) == 1.0
    assert breakdown.verifier_version == NL2SQLVerifier.VERSION == 2
    assert breakdown.extraction_applied is True
    assert breakdown.extraction_kind == "markdown_sql_fence"
    assert breakdown.scored_completion == "SELECT name FROM employees WHERE id = 1;"
    assert breakdown.as_dict()["extraction"] == {
        "applied": True,
        "kind": "markdown_sql_fence",
        "scored_completion": "SELECT name FROM employees WHERE id = 1;",
    }


def test_nl2sql_v2_uses_the_first_statement_inside_a_fenced_sql_block() -> None:
    verifier = NL2SQLVerifier(SCHEMA, [("Ada",)])
    completion = "```\nSELECT name FROM employees WHERE id = 1; SELECT 'later';\n```"

    assert verifier.score("find Ada", completion) == 1.0


def test_nl2sql_v2_keeps_unfenced_multi_statement_safety_rejection() -> None:
    verifier = NL2SQLVerifier(SCHEMA, [("Ada",)])
    completion = "SELECT name FROM employees WHERE id = 1; DELETE FROM employees"

    breakdown = verifier.score_breakdown("find Ada", completion)

    assert breakdown.final_score == 0.2
    assert breakdown.extraction_applied is False
    assert breakdown.failure_detail == "not_single_read_only_statement"


@pytest.mark.parametrize(
    ("completion", "expected_class", "expected_tiers"),
    [
        ("", "parse_failure", (0.0, 0.0, 0.0)),
        ("SELECT missing FROM employees", "execution_error", (0.2, 0.0, 0.0)),
        (
            "SELECT name FROM employees WHERE id = 2",
            "executable_not_full_pass",
            (0.2, 0.5, 0.0),
        ),
        (
            "SELECT name FROM employees WHERE id = 1",
            "full_pass",
            (0.2, 0.5, 1.0),
        ),
    ],
)
def test_score_breakdown_preserves_existing_score_and_exposes_tiers(
    completion: str, expected_class: str, expected_tiers: tuple[float, float, float]
) -> None:
    verifier = NL2SQLVerifier(SCHEMA, [("Ada",)])

    breakdown = verifier.score_breakdown("find Ada", completion)

    assert breakdown.final_score == verifier.score("find Ada", completion)
    assert breakdown.verifier_version == 2
    assert breakdown.failure_class == expected_class
    assert (
        breakdown.parse_score,
        breakdown.execution_score,
        breakdown.result_match_score,
    ) == expected_tiers


def test_score_breakdown_flags_existing_length_penalty_without_changing_match() -> None:
    verifier = NL2SQLVerifier(SCHEMA, [("Ada",)])
    completion = "SELECT name FROM employees WHERE id = 1 -- " + ("x" * 401)

    breakdown = verifier.score_breakdown("find Ada", completion)

    assert breakdown.final_score == 0.95
    assert breakdown.result_matched is True
    assert breakdown.failure_class == "executable_not_full_pass"
    assert breakdown.failure_detail == "length_penalized_exact_result"
