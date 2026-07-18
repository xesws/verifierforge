"""Best-effort SQLite traffic accounting for the product proxy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re

from app.db import RepositoryGateway, repository_gateway
from app.db.records import TrafficRequestRecord
from app.db.settings import DatabaseSettings


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = Path(__file__).with_name("traffic.db")
DEFAULT_PRICING_PATH = REPOSITORY_ROOT / "config" / "proxy_pricing.json"
_TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


@dataclass(frozen=True)
class TrafficRecord:
    timestamp: str
    system_prompt_hash: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    estimated_cost_usd: float
    route_path: str = "default"


def estimate_tokens(text: str) -> int:
    """Use a stable, intentionally simple estimate when an upstream omits usage."""
    return len(_TOKEN.findall(text))


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    pricing_path: Path = DEFAULT_PRICING_PATH,
) -> float:
    """Estimate USD cost from the editable default/per-model price table."""
    table = json.loads(Path(pricing_path).read_text(encoding="utf-8"))
    if not isinstance(table, dict):
        raise ValueError("proxy price table must be a JSON object")
    models = table.get("models", {})
    rates = models.get(model, table.get("default")) if isinstance(models, dict) else table.get("default")
    if not isinstance(rates, dict):
        raise ValueError("proxy price table requires default rates")
    input_rate = _rate(rates, "input_per_million_usd")
    output_rate = _rate(rates, "output_per_million_usd")
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts must be non-negative")
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000


def best_effort_cost(
    model: str, input_tokens: int, output_tokens: int, *, pricing_path: Path
) -> float:
    """Accounting configuration must not interrupt a customer completion."""
    try:
        return estimate_cost(model, input_tokens, output_tokens, pricing_path=pricing_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return 0.0


def record_traffic(
    record: TrafficRecord,
    *,
    db_path: Path | None = None,
    gateway: RepositoryGateway | None = None,
) -> bool:
    """Insert one metadata-only row without exposing a database failure upstream."""
    try:
        resolved = gateway or repository_gateway(
            DatabaseSettings.sqlite(db_path or DEFAULT_DB_PATH)
        )
        saved = TrafficRequestRecord(
            ts=_timestamp(record.timestamp),
            prompt_hash=record.system_prompt_hash,
            model=record.model,
            tokens_in=record.input_tokens,
            tokens_out=record.output_tokens,
            latency_ms=record.latency_ms,
            cost_usd=record.estimated_cost_usd,
            route_taken=record.route_path,
        )
        resolved.call(
            lambda repositories: repositories.traffic.append(saved)
        )
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("traffic timestamp must be ISO-8601") from None
    if parsed.tzinfo is None:
        raise ValueError("traffic timestamp must include a timezone")
    return parsed


def _rate(rates: dict[object, object], name: str) -> float:
    value = rates.get(name)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ValueError(f"proxy price table has invalid {name}")
    return float(value)
