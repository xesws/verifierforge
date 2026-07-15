"""Translate verl's file logger JSONL into VerifierForge metric records."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from core.contracts import MetricRecord
from core.storage.local import LocalStorage


_REWARD_KEYS = ("critic/score/mean", "critic/rewards/mean")
_ENTROPY_KEYS = ("actor/entropy",)


@dataclass(frozen=True)
class NormalizedMetric:
    """One training-step metric ready for the public append-only contract."""

    step: int
    reward_mean: float
    pass_at_1: float
    entropy: float


def _finite_number(value: Any) -> float | None:
    """Return a JSON logger scalar only when it is a finite float."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _validation_pass_at_1(data: Mapping[str, Any]) -> float | None:
    """Find verl's greedy validation accuracy despite its data-source prefix."""
    exact_suffixes = ("/acc/mean@1", "/acc/maj@1", "/acc/best@1")
    for key, value in data.items():
        if key.startswith("val-core/") and key.endswith(exact_suffixes):
            return _finite_number(value)

    # Keep the bridge compatible with a future verl metric spelling while
    # refusing unrelated validation scalars such as response length.
    for key, value in data.items():
        if key.startswith("val-core/") and "/acc/" in key and "/mean" in key:
            return _finite_number(value)
    return None


class VerlMetricBridge:
    """Tail a verl logger and append each training step at most once."""

    def __init__(self, storage: LocalStorage, job_id: str, logger_path: Path) -> None:
        self.storage = storage
        self.job_id = job_id
        self.logger_path = Path(logger_path)
        self._seen_steps, self._last_pass_at_1 = self._existing_metrics()

    def drain(self) -> list[NormalizedMetric]:
        """Append complete logger records not already present in ``metrics.jsonl``.

        Reading the whole file is deliberate: D2 has at most a few hundred
        records, and it makes restarts/truncation of verl's write-mode file
        logger safe without maintaining a fragile byte offset.
        """
        try:
            lines = self.logger_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []

        appended: list[NormalizedMetric] = []
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # A concurrent append can leave the final line incomplete.  It
                # remains available for the next drain instead of being lost.
                continue
            metric = self._normalize(event)
            if metric is None or metric.step in self._seen_steps:
                continue

            record = MetricRecord(
                job_id=self.job_id,
                step=metric.step,
                reward_mean=metric.reward_mean,
                pass_at_1=metric.pass_at_1,
                entropy=metric.entropy,
                timestamp=datetime.now(timezone.utc),
            )
            self.storage.append_metrics(self.job_id, record.model_dump(mode="json"))
            self._seen_steps.add(metric.step)
            appended.append(metric)
        return appended

    def _normalize(self, event: Mapping[str, Any]) -> NormalizedMetric | None:
        data = event.get("data")
        if not isinstance(data, Mapping):
            return None

        validation_value = _validation_pass_at_1(data)
        if validation_value is not None:
            self._last_pass_at_1 = validation_value

        step = event.get("step")
        if isinstance(step, bool):
            return None
        try:
            step = int(step)
        except (TypeError, ValueError):
            return None
        if step < 1:
            # Initial greedy validation is useful as the carried-forward
            # baseline, but Metrics starts at training step one.
            return None

        reward = next((_finite_number(data.get(key)) for key in _REWARD_KEYS if key in data), None)
        entropy = next((_finite_number(data.get(key)) for key in _ENTROPY_KEYS if key in data), None)
        if reward is None or entropy is None:
            return None

        return NormalizedMetric(
            step=step,
            reward_mean=reward,
            pass_at_1=self._last_pass_at_1 if self._last_pass_at_1 is not None else 0.0,
            entropy=entropy,
        )

    def _existing_metrics(self) -> tuple[set[int], float | None]:
        path = self.storage.root / self.job_id / "metrics.jsonl"
        seen: set[int] = set()
        latest_pass_at_1: float | None = None
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return seen, latest_pass_at_1

        for line in lines:
            try:
                record = json.loads(line)
                step = int(record["step"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            seen.add(step)
            pass_at_1 = _finite_number(record.get("pass_at_1"))
            if pass_at_1 is not None:
                latest_pass_at_1 = pass_at_1
        return seen, latest_pass_at_1
