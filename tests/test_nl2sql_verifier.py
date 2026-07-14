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
