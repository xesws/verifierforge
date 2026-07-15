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
