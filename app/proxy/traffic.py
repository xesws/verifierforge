"""Best-effort SQLite traffic accounting for the product proxy."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3


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


def record_traffic(record: TrafficRecord, *, db_path: Path) -> bool:
    """Insert one metadata-only row; return false instead of blocking on SQLite failure."""
    try:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as connection:
            _ensure_traffic_schema(connection)
            connection.execute(
                """
                INSERT INTO traffic (
                    timestamp, system_prompt_hash, model, input_tokens,
                    output_tokens, latency_ms, estimated_cost_usd, route_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.timestamp,
                    record.system_prompt_hash,
                    record.model,
                    record.input_tokens,
                    record.output_tokens,
                    record.latency_ms,
                    record.estimated_cost_usd,
                    record.route_path,
                ),
            )
        return True
    except (OSError, sqlite3.Error):
        return False


def _rate(rates: dict[object, object], name: str) -> float:
    value = rates.get(name)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ValueError(f"proxy price table has invalid {name}")
    return float(value)


def _ensure_traffic_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS traffic (
            id INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL,
            system_prompt_hash TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            latency_ms REAL NOT NULL,
            estimated_cost_usd REAL NOT NULL,
            route_path TEXT NOT NULL DEFAULT 'default'
        )
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(traffic)")}
    if "route_path" not in columns:
        connection.execute("ALTER TABLE traffic ADD COLUMN route_path TEXT NOT NULL DEFAULT 'default'")
