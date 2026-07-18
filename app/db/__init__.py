"""Async relational persistence for VerifierForge.

Relational facts live here. Checkpoints, raw metric streams, and evidence stay
behind :mod:`core.storage` and are intentionally outside this package.
"""

from .engine import DatabaseRuntime, create_database_runtime
from .gateway import RepositoryGateway, repository_gateway
from .migration import downgrade_database, migrate_sqlite, run_migrations
from .repositories import (
    DatabaseOperationError,
    RepositoryBundle,
    create_repositories,
)
from .settings import DatabaseBackend, DatabaseConfigurationError, DatabaseSettings

__all__ = [
    "DatabaseBackend",
    "DatabaseConfigurationError",
    "DatabaseRuntime",
    "RepositoryGateway",
    "DatabaseSettings",
    "DatabaseOperationError",
    "RepositoryBundle",
    "create_database_runtime",
    "repository_gateway",
    "create_repositories",
    "downgrade_database",
    "migrate_sqlite",
    "run_migrations",
]
