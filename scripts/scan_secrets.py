"""Fail when tracked text contains credential-shaped material."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
from typing import Iterable, Sequence


_SCHEMES = "(?:postgres(?:ql)?|mysql|mariadb)"
_SEPARATOR = ":" + "//"
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "credential-bearing database URL",
        re.compile(_SCHEMES + re.escape(_SEPARATOR) + r"[^\s/:@]+:[^\s@]+@", re.I),
    ),
    ("private key", re.compile("BEGIN " + r"(?:RSA |OPENSSH |EC )?PRIVATE KEY")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("known secret sentinel", re.compile("VF_SECRET_" + "SENTINEL_DO_NOT_LOG")),
)


def tracked_paths(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [root / value.decode() for value in result.stdout.split(b"\0") if value]


def scan_paths(paths: Iterable[Path]) -> list[tuple[Path, int, str]]:
    findings: list[tuple[Path, int, str]] = []
    for path in paths:
        if path.name == ".env" or path.name.startswith(".env."):
            findings.append((path, 1, "tracked environment file"))
            continue
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        if b"\0" in payload:
            continue
        text = payload.decode("utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in PATTERNS:
                if pattern.search(line):
                    findings.append((path, line_number, label))
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root = args.root.resolve()
    findings = scan_paths(tracked_paths(root))
    for path, line_number, label in findings:
        relative = path.relative_to(root)
        print(f"{relative}:{line_number}: {label}")
    if findings:
        print(f"secret scan failed: {len(findings)} finding(s); matched values were redacted")
        return 1
    print("secret scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
