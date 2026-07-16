#!/usr/bin/env python3
"""Build the Git-bound candidate-plus-seed population for the B1 probe."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from trainer.data.nl2sql_v1 import SCHEMA_SQL, _format_prompt  # noqa: E402


CANONICAL_SEED_IDS = tuple(f"v1-{index:03d}" for index in range(1, 51))


class PopulationError(ValueError):
    """Raised when source records cannot form the fixed B1 population."""


def build_parser() -> argparse.ArgumentParser:
    """Build the population-construction command-line interface."""
    parser = argparse.ArgumentParser(
        description="Build the deterministic NL2SQL B1 candidate-plus-seed population."
    )
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--seed-ref", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Build and atomically publish the probe population and its provenance."""
    args = build_parser().parse_args(argv)
    try:
        population, manifest = build_population(args.candidates, args.seed_ref)
        write_jsonl_atomic(args.output, population)
        manifest["population"] = _artifact_descriptor(args.output)
        write_json_atomic(args.manifest, manifest)
    except (OSError, PopulationError) as error:
        print(f"build_nl2sql_difficulty_population error: {error}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "candidate_count": manifest["candidate_source"]["record_count"],
                "original_seed_count": manifest["original_seed_source"]["record_count"],
                "population_count": len(population),
            },
            sort_keys=True,
        )
    )
    return 0


def build_population(
    candidate_path: Path, seed_ref: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return stable probe rows plus provenance without writing files."""
    candidate_bytes = candidate_path.read_bytes()
    candidates = _load_jsonl(candidate_bytes, candidate_path, label="candidate")
    seeds, seed_bytes = read_original_seed_rows(seed_ref)
    _validate_candidates(candidates)
    _validate_seeds(seeds)

    candidate_rows = [_candidate_population_row(candidate) for candidate in candidates]
    seed_rows = [_seed_population_row(seed, seed_ref) for seed in seeds]
    population = sorted(candidate_rows + seed_rows, key=lambda row: str(row["id"]))
    ids = [str(row["id"]) for row in population]
    if len(ids) != len(set(ids)):
        raise PopulationError("population IDs must be unique")

    manifest = {
        "schema_version": 1,
        "candidate_source": {
            "path": str(candidate_path),
            "sha256": hashlib.sha256(candidate_bytes).hexdigest(),
            "record_count": len(candidates),
        },
        "original_seed_source": {
            "git_ref": seed_ref,
            "git_path": "trainer/data/nl2sql_v1.jsonl",
            "sha256": hashlib.sha256(seed_bytes).hexdigest(),
            "record_count": len(seeds),
        },
        "population": {
            "record_count": len(population),
            "source_kinds": {"candidate": len(candidate_rows), "original_seed": len(seed_rows)},
        },
    }
    return population, manifest


def read_original_seed_rows(seed_ref: str) -> tuple[list[dict[str, Any]], bytes]:
    """Read the reviewed original fixture directly from the local Git object."""
    target = f"{seed_ref}:trainer/data/nl2sql_v1.jsonl"
    try:
        completed = subprocess.run(
            ["git", "show", target],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise PopulationError(f"cannot read original seeds at {target}") from error
    content = completed.stdout
    return _load_jsonl(content, Path(target), label="original seed"), content


def write_jsonl_atomic(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    """Atomically write canonical JSONL without exposing a partial population."""
    content = "".join(_canonical_json(dict(row)) + "\n" for row in rows)
    _write_text_atomic(path, content)


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically write canonical JSON provenance."""
    _write_text_atomic(path, _canonical_json(dict(payload)) + "\n")


def _load_jsonl(content: bytes, path: Path, *, label: str) -> list[dict[str, Any]]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise PopulationError(f"{label} JSONL must be UTF-8: {path}") from error
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise PopulationError(f"{label} line {line_number} is not valid JSON") from error
        if not isinstance(value, Mapping):
            raise PopulationError(f"{label} line {line_number} must be a JSON object")
        rows.append(dict(value))
    if not rows:
        raise PopulationError(f"{label} JSONL contains no records")
    return rows


def _validate_candidates(candidates: Sequence[Mapping[str, Any]]) -> None:
    required = {
        "id",
        "seed_id",
        "question",
        "prompt",
        "schema_sql",
        "expected_results",
        "reference_sql",
    }
    ids: set[str] = set()
    for index, candidate in enumerate(candidates, start=1):
        if not required <= candidate.keys():
            raise PopulationError(f"candidate {index} lacks required fields")
        candidate_id = candidate["id"]
        seed_id = candidate["seed_id"]
        if not isinstance(candidate_id, str) or not candidate_id:
            raise PopulationError(f"candidate {index} has an invalid id")
        if candidate_id in ids:
            raise PopulationError(f"candidate IDs are not unique: {candidate_id}")
        ids.add(candidate_id)
        if seed_id not in CANONICAL_SEED_IDS:
            raise PopulationError(f"candidate {candidate_id} has an unknown seed_id")
        if not all(isinstance(candidate[field], str) and candidate[field].strip() for field in required - {"expected_results", "seed_id"}):
            raise PopulationError(f"candidate {candidate_id} has an invalid text field")
        if not isinstance(candidate["expected_results"], list):
            raise PopulationError(f"candidate {candidate_id} has invalid expected_results")


def _validate_seeds(seeds: Sequence[Mapping[str, Any]]) -> None:
    by_id: dict[str, Mapping[str, Any]] = {}
    for index, seed in enumerate(seeds, start=1):
        seed_id = seed.get("id")
        if not isinstance(seed_id, str) or seed_id not in CANONICAL_SEED_IDS:
            raise PopulationError(f"original seed {index} has an invalid id")
        if seed_id in by_id:
            raise PopulationError(f"original seed IDs are not unique: {seed_id}")
        if not all(isinstance(seed.get(field), str) and seed[field].strip() for field in ("question", "reference_sql")):
            raise PopulationError(f"original seed {seed_id} has an invalid text field")
        if not isinstance(seed.get("expected_results"), list):
            raise PopulationError(f"original seed {seed_id} has invalid expected_results")
        by_id[seed_id] = seed
    if tuple(sorted(by_id)) != CANONICAL_SEED_IDS:
        raise PopulationError("original seeds must contain exactly v1-001 through v1-050")


def _candidate_population_row(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": f"candidate:{candidate['id']}",
        "source_record_id": candidate["id"],
        "seed_id": candidate["seed_id"],
        "source_kind": "candidate",
        "question": candidate["question"],
        "prompt": candidate["prompt"],
        "schema_sql": candidate["schema_sql"],
        "expected_results": candidate["expected_results"],
        "reference_sql": candidate["reference_sql"],
    }


def _seed_population_row(seed: Mapping[str, Any], seed_ref: str) -> dict[str, Any]:
    question = str(seed["question"])
    return {
        "id": f"original:{seed['id']}",
        "source_record_id": seed["id"],
        "seed_id": seed["id"],
        "source_kind": "original_seed",
        "source_git_ref": seed_ref,
        "question": question,
        "prompt": _format_prompt(question),
        "schema_sql": SCHEMA_SQL,
        "expected_results": seed["expected_results"],
        "reference_sql": seed["reference_sql"],
    }


def _artifact_descriptor(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "record_count": sum(1 for line in raw.splitlines() if line.strip()),
    }


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


if __name__ == "__main__":
    raise SystemExit(main())
