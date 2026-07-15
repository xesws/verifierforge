"""Convert the reviewed V1 fixture into verl's ignored remote Parquet inputs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


DATA_SOURCE = "nl2sql_v1"


@dataclass(frozen=True)
class VerlInputPaths:
    train: Path
    validation: Path


def build_verl_rows(cases: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Map reviewed JSONL cases to the small schema expected by verl's dataset."""
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        try:
            case_id = case["id"]
            prompt = case["prompt"]
            schema_sql = case["schema_sql"]
            expected_results = case["expected_results"]
        except KeyError as error:
            raise ValueError(f"V1 case is missing {error.args[0]!r}") from error
        if not isinstance(case_id, str) or not isinstance(prompt, str) or not isinstance(schema_sql, str):
            raise ValueError(f"V1 case {case_id!r} has invalid text fields")
        if not isinstance(expected_results, list):
            raise ValueError(f"V1 case {case_id!r} expected_results must be a list")

        ground_truth = json.dumps(
            {"schema_sql": schema_sql, "expected_results": expected_results},
            separators=(",", ":"),
        )
        rows.append(
            {
                "data_source": DATA_SOURCE,
                "prompt": [{"role": "user", "content": prompt}],
                "reward_model": {"style": "rule", "ground_truth": ground_truth},
                "extra_info": {"index": index, "case_id": case_id},
            }
        )
    return rows


def write_parquet(rows: Sequence[Mapping[str, Any]], destination: Path) -> Path:
    """Write a parquet input atomically; pyarrow arrives with verl on RunPod."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as parquet
    except ModuleNotFoundError as error:  # pragma: no cover - executed on the trainer pod
        raise RuntimeError("GRPO data conversion requires pyarrow (installed with verl)") from error

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    table = pa.Table.from_pylist(list(rows))
    parquet.write_table(table, temporary)
    os.replace(temporary, destination)
    return destination


def prepare_v1_inputs(runs_root: Path, job_id: str) -> VerlInputPaths:
    """Create the deterministic 40/10 remote-only Parquet split for one job."""
    from trainer.data.nl2sql_v1 import split_cases

    train_cases, validation_cases = split_cases(seed=42)
    if len(train_cases) != 40 or len(validation_cases) != 10:
        raise ValueError("D2 V1 fixture must split into exactly 40 train and 10 validation cases")

    input_dir = Path(runs_root) / job_id / "input"
    train = write_parquet(build_verl_rows(train_cases), input_dir / "train.parquet")
    validation = write_parquet(build_verl_rows(validation_cases), input_dir / "validation.parquet")
    return VerlInputPaths(train=train, validation=validation)
