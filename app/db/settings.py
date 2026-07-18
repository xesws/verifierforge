"""Sanitized database backend selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from os import PathLike
from pathlib import Path
from typing import Mapping

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


class DatabaseConfigurationError(RuntimeError):
    """A stable error that never contains a connection string."""


class DatabaseBackend(str, Enum):
    SQLITE = "sqlite"
    POSTGRES = "postgres"


@dataclass(frozen=True)
class DatabaseSettings:
    """Resolved backend settings with secret-safe representation."""

    backend: DatabaseBackend
    _url: URL = field(repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "DatabaseSettings":
        import os

        values = os.environ if environ is None else environ
        raw_backend = values.get("VF_DB_BACKEND", DatabaseBackend.SQLITE.value).strip().lower()
        try:
            backend = DatabaseBackend(raw_backend)
        except ValueError:
            raise DatabaseConfigurationError(
                "VF_DB_BACKEND must be 'sqlite' or 'postgres'"
            ) from None

        if backend is DatabaseBackend.SQLITE:
            raw_path: str | PathLike[str] = values.get(
                "VF_PROXY_DB_PATH", "app/proxy/traffic.db"
            )
            path = Path(raw_path).expanduser()
            return cls(
                backend=backend,
                _url=URL.create("sqlite+aiosqlite", database=str(path)),
            )

        raw_url = values.get("SUPABASE_DB_URL", "").strip()
        if not raw_url:
            raise DatabaseConfigurationError(
                "SUPABASE_DB_URL is required when VF_DB_BACKEND=postgres"
            )
        try:
            parsed = make_url(raw_url)
        except (ArgumentError, TypeError, ValueError):
            raise DatabaseConfigurationError("SUPABASE_DB_URL is invalid") from None
        if parsed.get_backend_name() not in {"postgres", "postgresql"}:
            raise DatabaseConfigurationError("SUPABASE_DB_URL must be a PostgreSQL URL")
        if not parsed.host or not parsed.database:
            raise DatabaseConfigurationError("SUPABASE_DB_URL is incomplete")
        return cls(backend=backend, _url=parsed.set(drivername="postgresql+asyncpg"))

    @classmethod
    def sqlite(cls, path: Path | str) -> "DatabaseSettings":
        """Build explicit test/local settings without reading process state."""

        return cls(
            backend=DatabaseBackend.SQLITE,
            _url=URL.create("sqlite+aiosqlite", database=str(Path(path).expanduser())),
        )

    @property
    def url(self) -> URL:
        return self._url

    @property
    def safe_name(self) -> str:
        return self.backend.value

    def __str__(self) -> str:
        return f"DatabaseSettings(backend={self.backend.value})"
