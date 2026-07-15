from core.rewards.nl2sql import NL2SQLVerifier
from trainer.data.nl2sql_v1 import (
    SPLIT_SEED,
    SCHEMA_SQL,
    load_cases,
    split_cases,
)


def test_v1_fixture_has_fifty_sorted_reviewable_cases() -> None:
    cases = load_cases()

    assert len(cases) == 50
    assert [case["id"] for case in cases] == [f"v1-{index:03d}" for index in range(1, 51)]
    assert all(case["schema_sql"] == SCHEMA_SQL for case in cases)
    assert all("Schema:" in case["prompt"] for case in cases)
    assert all(isinstance(case["expected_results"], list) for case in cases)


def test_v1_fixture_split_is_deterministic_and_disjoint() -> None:
    cases = load_cases()
    train, validation = split_cases(cases, seed=SPLIT_SEED)

    assert len(train) == 40
    assert [case["id"] for case in validation] == [
        "v1-006",
        "v1-035",
        "v1-007",
        "v1-009",
        "v1-015",
        "v1-016",
        "v1-018",
        "v1-002",
        "v1-008",
        "v1-041",
    ]
    assert {case["id"] for case in train}.isdisjoint(
        {case["id"] for case in validation}
    )
    assert {case["id"] for case in train + validation} == {
        case["id"] for case in cases
    }


def test_every_v1_reference_sql_matches_its_expected_result_set() -> None:
    for case in load_cases():
        verifier = NL2SQLVerifier(case["schema_sql"], case["expected_results"])

        assert verifier.score(case["prompt"], case["reference_sql"]) == 1.0, case["id"]
