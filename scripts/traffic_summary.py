"""Print the compact D5 Discover grouping from product traffic metadata."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.db import repository_gateway
from app.db.settings import DatabaseSettings
from app.proxy.traffic import DEFAULT_DB_PATH


def summarize(db_path: Path) -> list[tuple[str, int, int, float]]:
    gateway = repository_gateway(DatabaseSettings.sqlite(db_path))
    rows = gateway.call(
        lambda repositories: repositories.traffic.summarize_by_prompt_hash()
    )
    return [
        (row.prompt_hash, row.request_count, row.total_tokens, row.total_cost_usd)
        for row in rows
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    for prompt_hash, requests, tokens, cost in summarize(args.db):
        print(f"{prompt_hash}\trequests={requests}\ttokens={tokens}\testimated_cost_usd={cost:.8f}")


if __name__ == "__main__":
    main()
