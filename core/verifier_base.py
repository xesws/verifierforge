"""Base interface for programmatic reward verifiers."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Verifier(ABC):
    """Scores a completion and advertises the scoring rubric."""

    @abstractmethod
    def score(self, prompt: str, completion: str) -> float:
        """Return a reward in the inclusive range [0, 1]."""

    @classmethod
    @abstractmethod
    def tiers(cls) -> dict[float, str]:
        """Return score thresholds mapped to their rubric descriptions."""
