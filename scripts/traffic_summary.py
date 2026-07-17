"""Print the compact D5 Discover grouping from product traffic SQLite metadata."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3

from app.proxy.traffic import DEFAULT_DB_PATH


def summarize(db_path: Path) -> list[tuple[str, int, int, float]]:
    with sqlite3.connect(db_path) as connection:
        return connection.execute(
            """
            SELECT system_prompt_hash, COUNT(*), SUM(input_tokens + output_tokens), SUM(estimated_cost_usd)
            FROM traffic GROUP BY system_prompt_hash ORDER BY system_prompt_hash
            """
        ).fetchall()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    for prompt_hash, requests, tokens, cost in summarize(args.db):
        print(f"{prompt_hash}\trequests={requests}\ttokens={tokens}\testimated_cost_usd={cost:.8f}")


if __name__ == "__main__":
    main()
