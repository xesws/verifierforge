#!/usr/bin/env python3
"""Deprecated command name for the authoritative U3 three-piece freezer."""

from __future__ import annotations

from collections.abc import Sequence
import sys

from scripts import freeze_three_piece


DEPRECATION = (
    "freeze_nl2sql is deprecated; delegating to the U3 three-piece freezer "
    "(training pool + held-out set + verifier identity)"
)


def main(argv: Sequence[str] | None = None) -> int:
    """Delegate without preserving the obsolete pre-U3 freeze semantics."""
    print(DEPRECATION, file=sys.stderr)
    return freeze_three_piece.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
