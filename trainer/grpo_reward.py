"""verl reward adapter for the reviewed V1 NL-to-SQL fixture."""

from __future__ import annotations

import json
from typing import Any, Mapping

from core.rewards.nl2sql import NL2SQLVerifier


def _ground_truth(value: Any) -> Mapping[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise TypeError("NL2SQL ground truth must be a mapping or JSON object")
    if not isinstance(value.get("schema_sql"), str):
        raise TypeError("NL2SQL ground truth requires schema_sql")
    if not isinstance(value.get("expected_results"), list):
        raise TypeError("NL2SQL ground truth requires expected_results")
    return value


def compute_score(
    data_source: str,
    solution_str: str,
    ground_truth: Any,
    extra_info: Mapping[str, Any] | None = None,
    **_: Any,
) -> dict[str, float]:
    """Return tiered SQL reward plus an exact-result accuracy signal for verl.

    Reward code runs in a worker process.  It must turn malformed fixture data or
    an unexpected completion into zero reward rather than destabilising the
    training process.
    """
    del extra_info
    if data_source != "nl2sql_v1":
        return {"score": 0.0, "acc": 0.0}

    try:
        expected = _ground_truth(ground_truth)
        verifier = NL2SQLVerifier(expected["schema_sql"], expected["expected_results"])
        score = float(verifier.score("", solution_str))
    except Exception:
        return {"score": 0.0, "acc": 0.0}

    # Exact SQL receives 1.0; a correctly executing query over 400 characters
    # receives 0.95 after the documented length penalty and still counts as a
    # task pass for the greedy validation curve.
    return {"score": score, "acc": 1.0 if score >= 0.95 else 0.0}
