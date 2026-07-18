"""Small append-only paid-LLM budget guard used by smoke and Gate C."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


PROVIDER_CAPS_USD = {"openai": 8.0, "openrouter": 2.0}
OPENAI_DECLARED_CREDIT_USD = 10.0
OPENAI_REQUIRED_RESERVE_USD = 2.0


class LLMBudgetError(RuntimeError):
    """Raised before a request that could exceed the approved spend ceiling."""


@dataclass(frozen=True)
class BudgetReceipt:
    provider: str
    charged_usd: float
    cost_basis: str
    provider_reported_cost_usd: float | None


class CostLedger:
    """Append safe cost metadata; unknown provider cost is charged at reservation."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def authorize(self, provider: str, reservation_usd: float) -> None:
        provider = _provider(provider)
        if reservation_usd <= 0:
            raise ValueError("LLM cost reservation must be positive")
        projected = self.spent(provider) + reservation_usd
        if projected > PROVIDER_CAPS_USD[provider] + 1e-12:
            raise LLMBudgetError(
                f"{provider} request would exceed the ${PROVIDER_CAPS_USD[provider]:.2f} cap"
            )
        if provider == "openai" and OPENAI_DECLARED_CREDIT_USD - projected < OPENAI_REQUIRED_RESERVE_USD - 1e-12:
            raise LLMBudgetError(
                f"OpenAI request would breach the owner-required "
                f"${OPENAI_REQUIRED_RESERVE_USD:.2f} reserve"
            )

    def count_status_prefix(self, provider: str, prefix: str) -> int:
        """Count durable attempts without exposing request data or credentials."""
        provider = _provider(provider)
        if not self.path.exists():
            return 0
        count = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("provider") == provider and str(row.get("status", "")).startswith(prefix):
                count += 1
        return count

    def record(
        self,
        *,
        provider: str,
        reservation_usd: float,
        provider_reported_cost_usd: float | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
        status: str,
    ) -> BudgetReceipt:
        provider = _provider(provider)
        self.authorize(provider, reservation_usd)
        reported = (
            float(provider_reported_cost_usd)
            if provider_reported_cost_usd is not None
            and provider_reported_cost_usd >= 0
            else None
        )
        charged = min(reported, reservation_usd) if reported is not None else reservation_usd
        basis = "provider_reported" if reported is not None else "reservation_upper_bound"
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reservation_usd": reservation_usd,
            "provider_reported_cost_usd": reported,
            "charged_usd": charged,
            "cost_basis": basis,
            "status": status,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        return BudgetReceipt(provider, charged, basis, reported)

    def spent(self, provider: str) -> float:
        provider = _provider(provider)
        if not self.path.exists():
            return 0.0
        total = 0.0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("provider") == provider:
                total += float(row.get("charged_usd", 0.0))
        return total


def _provider(value: str) -> str:
    provider = value.strip().lower()
    if provider not in PROVIDER_CAPS_USD:
        raise ValueError("unsupported LLM budget provider")
    return provider
