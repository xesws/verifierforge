from pathlib import Path

import pytest

from app.gpt.budget import CostLedger, LLMBudgetError


def test_unknown_cost_is_charged_at_reserved_upper_bound(tmp_path: Path) -> None:
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    receipt = ledger.record(
        provider="openai",
        reservation_usd=0.25,
        provider_reported_cost_usd=None,
        model="gpt-5.6-luna",
        input_tokens=10,
        output_tokens=2,
        status="ok",
    )

    assert receipt.charged_usd == 0.25
    assert receipt.cost_basis == "reservation_upper_bound"
    assert ledger.spent("openai") == 0.25


def test_openai_budget_preserves_owner_reserve(tmp_path: Path) -> None:
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    for _ in range(2):
        ledger.record(
            provider="openai",
            reservation_usd=1.0,
            provider_reported_cost_usd=None,
            model="gpt-5.6-luna",
            input_tokens=1,
            output_tokens=1,
            status="ok",
        )

    with pytest.raises(LLMBudgetError, match="reserve|cap"):
        ledger.authorize("openai", 1.01)


def test_openrouter_has_independent_two_dollar_cap(tmp_path: Path) -> None:
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    ledger.record(
        provider="openrouter",
        reservation_usd=1.75,
        provider_reported_cost_usd=0.5,
        model="z-ai/glm-5.2",
        input_tokens=2,
        output_tokens=2,
        status="ok",
    )

    assert ledger.spent("openrouter") == 0.5
    with pytest.raises(LLMBudgetError, match="cap"):
        ledger.authorize("openrouter", 1.51)
