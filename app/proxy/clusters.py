"""Stable product-cluster identities shared by traffic generation and routing."""

from __future__ import annotations

import hashlib


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
