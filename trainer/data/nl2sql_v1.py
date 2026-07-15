"""Deterministic V1 employee/department/project NL-to-SQL fixture helpers."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Mapping, Sequence


FIXTURE_PATH = Path(__file__).with_name("nl2sql_v1.jsonl")
SPLIT_SEED = 42
TRAIN_CASE_COUNT = 40
TOTAL_CASE_COUNT = 50

SCHEMA_SQL = """
CREATE TABLE departments (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, location TEXT NOT NULL, budget INTEGER NOT NULL
);
INSERT INTO departments VALUES
    (1, 'Engineering', 'San Francisco', 500000),
    (2, 'Research', 'New York', 400000),
    (3, 'Sales', 'Chicago', 300000),
    (4, 'Operations', 'Austin', 250000),
    (5, 'Design', 'Remote', 200000);

CREATE TABLE employees (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, department_id INTEGER NOT NULL,
    title TEXT NOT NULL, salary INTEGER NOT NULL, hire_date TEXT NOT NULL, active INTEGER NOT NULL
);
INSERT INTO employees VALUES
    (1, 'Ada', 1, 'Engineer', 150000, '2020-01-15', 1),
    (2, 'Grace', 1, 'Staff Engineer', 170000, '2018-06-01', 1),
    (3, 'Linus', 2, 'Researcher', 160000, '2019-09-10', 1),
    (4, 'Margaret', 2, 'Researcher', 155000, '2021-03-20', 1),
    (5, 'Alan', 3, 'Account Executive', 130000, '2017-11-05', 1),
    (6, 'Barbara', 3, 'Sales Manager', 145000, '2016-04-12', 1),
    (7, 'Donald', 4, 'Operations Manager', 135000, '2020-08-08', 1),
    (8, 'Edsger', 4, 'Analyst', 110000, '2022-02-28', 0),
    (9, 'Hedy', 5, 'Designer', 125000, '2019-12-01', 1),
    (10, 'Ken', 5, 'Design Lead', 140000, '2018-07-19', 1),
    (11, 'Frances', 1, 'Engineer', 120000, '2023-01-05', 1),
    (12, 'Claude', 2, 'Intern', 80000, '2024-05-01', 1);

CREATE TABLE projects (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, department_id INTEGER NOT NULL,
    lead_employee_id INTEGER NOT NULL, budget INTEGER NOT NULL, status TEXT NOT NULL
);
INSERT INTO projects VALUES
    (1, 'Atlas', 1, 2, 200000, 'active'),
    (2, 'Beacon', 2, 3, 150000, 'active'),
    (3, 'Comet', 3, 6, 90000, 'planned'),
    (4, 'Delta', 4, 7, 110000, 'active'),
    (5, 'Echo', 5, 10, 75000, 'complete'),
    (6, 'Forge', 1, 1, 180000, 'active'),
    (7, 'Genome', 2, 4, 210000, 'planned'),
    (8, 'Horizon', 3, 5, 60000, 'complete');

CREATE TABLE employee_projects (
    employee_id INTEGER NOT NULL, project_id INTEGER NOT NULL, hours INTEGER NOT NULL
);
INSERT INTO employee_projects VALUES
    (1, 1, 50), (1, 6, 70), (2, 1, 80), (2, 6, 30),
    (3, 2, 100), (3, 7, 20), (4, 2, 40), (4, 7, 90),
    (5, 3, 75), (5, 8, 25), (6, 3, 100), (7, 4, 120),
    (8, 4, 20), (9, 5, 60), (10, 5, 100), (11, 1, 30),
    (11, 6, 60), (12, 2, 10);
""".strip()


# The model needs table and column names to form a query, not a second copy of
# every execution fixture. Keeping inserts out of this prompt makes the D2
# 1024-token rollout budget meaningful while `SCHEMA_SQL` remains authoritative
# for reward execution.
PROMPT_SCHEMA_SQL = """
CREATE TABLE departments (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, location TEXT NOT NULL, budget INTEGER NOT NULL
);
CREATE TABLE employees (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, department_id INTEGER NOT NULL,
    title TEXT NOT NULL, salary INTEGER NOT NULL, hire_date TEXT NOT NULL, active INTEGER NOT NULL
);
CREATE TABLE projects (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, department_id INTEGER NOT NULL,
    lead_employee_id INTEGER NOT NULL, budget INTEGER NOT NULL, status TEXT NOT NULL
);
CREATE TABLE employee_projects (
    employee_id INTEGER NOT NULL, project_id INTEGER NOT NULL, hours INTEGER NOT NULL
);
""".strip()


def _format_prompt(question: str) -> str:
    return (
        "Return exactly one read-only SQLite SELECT or WITH statement. "
        "Do not include an explanation.\n\n"
        f"Schema:\n{PROMPT_SCHEMA_SQL}\n\n"
        f"Question: {question}\nSQL:"
    )


def load_cases(path: Path | None = None) -> list[dict[str, Any]]:
    """Load the reviewed fixture and attach the shared SQLite schema to each case."""
    fixture_path = path or FIXTURE_PATH
    cases: list[dict[str, Any]] = []
    required_fields = {"id", "question", "reference_sql", "expected_results"}

    with fixture_path.open(encoding="utf-8") as fixture:
        for line_number, line in enumerate(fixture, start=1):
            if not line.strip():
                continue
            try:
                raw_case = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"invalid JSON in {fixture_path} line {line_number}"
                ) from error

            if not isinstance(raw_case, dict) or not required_fields <= raw_case.keys():
                raise ValueError(
                    f"fixture line {line_number} must contain {sorted(required_fields)}"
                )
            if not isinstance(raw_case["id"], str) or not raw_case["id"]:
                raise ValueError(f"fixture line {line_number} has an invalid id")
            if not isinstance(raw_case["question"], str) or not raw_case["question"]:
                raise ValueError(f"fixture line {line_number} has an invalid question")
            if not isinstance(raw_case["reference_sql"], str) or not raw_case["reference_sql"]:
                raise ValueError(f"fixture line {line_number} has invalid reference_sql")
            if not isinstance(raw_case["expected_results"], list):
                raise ValueError(f"fixture line {line_number} has invalid expected_results")

            cases.append(
                {
                    "id": raw_case["id"],
                    "prompt": _format_prompt(raw_case["question"]),
                    "schema_sql": SCHEMA_SQL,
                    "expected_results": raw_case["expected_results"],
                    "reference_sql": raw_case["reference_sql"],
                }
            )

    case_ids = [case["id"] for case in cases]
    if len(cases) != TOTAL_CASE_COUNT:
        raise ValueError(f"expected {TOTAL_CASE_COUNT} V1 cases, found {len(cases)}")
    if case_ids != sorted(case_ids) or len(case_ids) != len(set(case_ids)):
        raise ValueError("V1 fixture ids must be unique and sorted")
    return cases


def split_cases(
    cases: Sequence[Mapping[str, Any]] | None = None,
    seed: int = SPLIT_SEED,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return the stable 40/10 split used by the D2 GRPO smoke run."""
    candidates = [dict(case) for case in (cases if cases is not None else load_cases())]
    if len(candidates) != TOTAL_CASE_COUNT:
        raise ValueError(f"expected {TOTAL_CASE_COUNT} cases for the V1 split")

    candidates.sort(key=lambda case: str(case["id"]))
    random.Random(seed).shuffle(candidates)
    return candidates[:TRAIN_CASE_COUNT], candidates[TRAIN_CASE_COUNT:]
