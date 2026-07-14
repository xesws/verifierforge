"""The small storage contract used by trainers and APIs."""

from abc import ABC, abstractmethod
from pathlib import Path


class Storage(ABC):
    @abstractmethod
    def save_checkpoint(self, job_id: str, step: int, path: Path) -> None:
        """Persist a checkpoint for a training step."""

    @abstractmethod
    def load_latest_checkpoint(self, job_id: str) -> Path | None:
        """Return the most recent checkpoint directory, if one exists."""

    @abstractmethod
    def append_metrics(self, job_id: str, record: dict) -> None:
        """Append one metrics record for a job."""

    @abstractmethod
    def put_artifact(self, job_id: str, name: str, path: Path) -> None:
        """Store an artifact under a job-relative name."""

    @abstractmethod
    def get_artifact(self, job_id: str, name: str, dest: Path) -> Path:
        """Copy an artifact to ``dest`` and return its resulting path."""
