"""Best-effort sidecar scorer for sampled, routed NL2SQL completions."""

from __future__ import annotations

from pathlib import Path

from app.db import RepositoryGateway
from app.proxy.frozen_nl2sql import FROZEN_TRAINING_POOL, case_for_prompt
from app.proxy.routing import record_guardian_score
from core.rewards.nl2sql import NL2SQLVerifier


def score_tuned_sql_completion(
    *,
    cluster_id: str,
    prompt: str,
    completion: str,
    db_path: Path | None = None,
    gateway: RepositoryGateway | None = None,
    pool_path: Path = FROZEN_TRAINING_POOL,
    rolling_window: int = 20,
) -> bool:
    """Score one known frozen SQL prompt without ever raising into the proxy."""
    try:
        case = case_for_prompt(prompt, pool_path=pool_path)
        if case is None:
            return False
        score = NL2SQLVerifier(case.schema_sql, case.expected_results).score(prompt, completion)
        record_guardian_score(
            cluster_id,
            score,
            db_path=db_path,
            gateway=gateway,
            rolling_window=rolling_window,
        )
    except Exception:
        return False
    return True
