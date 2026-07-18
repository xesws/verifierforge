"""Stable product-cluster identities shared by traffic generation and routing."""

from __future__ import annotations

import hashlib

from core.contracts import Cluster, ClusterStatus


CLUSTER_CATALOG: tuple[Cluster, ...] = (
    Cluster(
        cluster_id="support-ticket-extraction",
        name="Support ticket extraction",
        monthly_calls=240_000,
        monthly_cost_usd=4_800.0,
        trainable=True,
        status=ClusterStatus.LIVE,
        job_id="nl2sql-gain",
    ),
    Cluster(
        cluster_id="invoice-field-extraction",
        name="Invoice field extraction",
        monthly_calls=180_000,
        monthly_cost_usd=6_000.0,
        trainable=True,
        status=ClusterStatus.DISCOVERED,
    ),
    Cluster(
        cluster_id="data-pull-sql",
        name="Data Pull SQL",
        monthly_calls=95_000,
        monthly_cost_usd=5_500.0,
        trainable=True,
        status=ClusterStatus.DISCOVERED,
    ),
)

_CLUSTERS_BY_ID = {cluster.cluster_id: cluster for cluster in CLUSTER_CATALOG}


def list_cluster_profiles() -> list[Cluster]:
    """Return independent copies in the stable product display order."""
    return [cluster.model_copy(deep=True) for cluster in CLUSTER_CATALOG]


def cluster_profile(cluster_id: str) -> Cluster:
    """Return one stable cluster profile without exposing shared mutable state."""
    try:
        return _CLUSTERS_BY_ID[cluster_id].model_copy(deep=True)
    except KeyError as error:
        raise KeyError(cluster_id) from error


SYSTEM_PROMPTS_BY_CLUSTER = {
    "support-ticket-extraction": "Extract issue, account, order identifier, urgency, and requested action as JSON.",
    "invoice-field-extraction": "Extract invoice number, vendor, due date, currency, and total as JSON.",
    "data-pull-sql": "Return exactly one read-only SQL SELECT or WITH statement. Do not include an explanation.",
}


def system_prompt_hash(system_prompt: str) -> str:
    """Return the storage key used by proxy traffic and cluster routing."""
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()


_HASH_TO_CLUSTER = {
    system_prompt_hash(prompt): cluster_id
    for cluster_id, prompt in SYSTEM_PROMPTS_BY_CLUSTER.items()
}


def cluster_id_for_system_prompt(system_prompt: str) -> str | None:
    """Map known product-system prompts to their stable cluster IDs."""
    return _HASH_TO_CLUSTER.get(system_prompt_hash(system_prompt))
