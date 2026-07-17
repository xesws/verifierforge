"""Read the committed, reviewer-safe D4 demo artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.contracts import Job, LivePassRate, MetricRecord, Metrics, RoutingState


class ArtifactDataError(RuntimeError):
    """Committed demo data is absent, malformed, or internally inconsistent."""


class ArtifactStore:
    """Small read-only store used by ``VF_API_DATA_MODE=artifacts``."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        index = self._read_json(self.root / "index.json")
        entries = index.get("jobs") if isinstance(index, dict) else None
        if not isinstance(entries, list):
            raise ArtifactDataError("demo artifact index must contain a jobs list")
        self._entries: dict[str, dict[str, str]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ArtifactDataError("demo artifact job entry must be an object")
            job_id = entry.get("job_id")
            job_path = entry.get("job_path")
            metrics_path = entry.get("metrics_path")
            if not all(isinstance(value, str) and value for value in (job_id, job_path, metrics_path)):
                raise ArtifactDataError("demo artifact job entry is incomplete")
            if job_id in self._entries:
                raise ArtifactDataError(f"duplicate demo artifact job id: {job_id}")
            self._entries[job_id] = {"job_path": job_path, "metrics_path": metrics_path}
        clusters_path = index.get("clusters_path") if isinstance(index, dict) else None
        self._clusters_path = clusters_path if isinstance(clusters_path, str) else "clusters.json"

    def list_jobs(self) -> list[dict[str, str]]:
        return [
            {"job_id": job.job_id, "status": job.status.value}
            for job in (self.job(job_id) for job_id in sorted(self._entries))
        ]

    def job(self, job_id: str) -> Job:
        entry = self._entry(job_id)
        payload = self._read_json(self._path(entry["job_path"]))
        try:
            job = Job.model_validate(payload)
        except Exception as error:  # Pydantic supplies an actionable validation detail.
            raise ArtifactDataError(f"invalid demo job {job_id!r}") from error
        if job.job_id != job_id:
            raise ArtifactDataError(f"demo job id mismatch for {job_id!r}")
        metrics = self.metrics(job_id)
        if job.metrics != metrics:
            raise ArtifactDataError(f"demo job metrics do not match JSONL for {job_id!r}")
        return job

    def metrics(self, job_id: str) -> Metrics:
        entry = self._entry(job_id)
        metrics_path = self._path(entry["metrics_path"])
        try:
            lines = metrics_path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise ArtifactDataError(f"cannot read demo metrics for {job_id!r}") from error
        records: list[MetricRecord] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                records.append(MetricRecord.model_validate_json(line))
            except Exception as error:
                raise ArtifactDataError(f"invalid demo metric for {job_id!r}") from error
        if any(record.job_id != job_id for record in records):
            raise ArtifactDataError(f"demo metric job id mismatch for {job_id!r}")
        return Metrics(
            steps=[record.step for record in records],
            reward_mean=[record.reward_mean for record in records],
            pass_at_1=[record.pass_at_1 for record in records],
            entropy=[record.entropy for record in records],
        )

    def routing(self, cluster_id: str) -> RoutingState:
        payload = self._clusters().get("routing", {})
        if not isinstance(payload, dict) or cluster_id not in payload:
            raise KeyError(cluster_id)
        try:
            return RoutingState.model_validate(payload[cluster_id])
        except Exception as error:
            raise ArtifactDataError(f"invalid demo routing for {cluster_id!r}") from error

    def live_pass_rate(self, cluster_id: str) -> LivePassRate:
        payload = self._clusters().get("live_pass_rate", {})
        if not isinstance(payload, dict) or cluster_id not in payload:
            raise KeyError(cluster_id)
        try:
            return LivePassRate.model_validate(payload[cluster_id])
        except Exception as error:
            raise ArtifactDataError(f"invalid demo live pass rate for {cluster_id!r}") from error

    def _entry(self, job_id: str) -> dict[str, str]:
        try:
            return self._entries[job_id]
        except KeyError as error:
            raise KeyError(job_id) from error

    def _clusters(self) -> dict[str, Any]:
        payload = self._read_json(self._path(self._clusters_path))
        if not isinstance(payload, dict):
            raise ArtifactDataError("demo cluster artifact must be an object")
        return payload

    def _path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        if candidate.parent != self.root.resolve() and self.root.resolve() not in candidate.parents:
            raise ArtifactDataError("demo artifact path escapes its root")
        return candidate

    @staticmethod
    def _read_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ArtifactDataError(f"cannot read demo artifact: {path}") from error
