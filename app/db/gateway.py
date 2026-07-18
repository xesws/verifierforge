"""Thread-safe synchronous access to the async repository bundle.

The product proxy and Forge Agent are intentionally synchronous today.  This
gateway keeps one async engine on one private event loop so those callers can
use the shared repository layer without opening dialect-specific connections.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
import threading
from typing import TypeVar

from .engine import DatabaseRuntime, create_database_runtime
from .migration import run_migrations
from .repositories import DatabaseOperationError, RepositoryBundle, create_repositories
from .settings import DatabaseBackend, DatabaseSettings


T = TypeVar("T")
Operation = Callable[[RepositoryBundle], Awaitable[T]]


class RepositoryGateway:
    """Own one repository runtime and execute operations on its event loop."""

    def __init__(self, settings: DatabaseSettings | None = None) -> None:
        self.settings = settings or DatabaseSettings.from_env()
        try:
            if self.settings.backend is DatabaseBackend.SQLITE:
                run_migrations(self.settings)
        except Exception:
            raise DatabaseOperationError("database initialization failed") from None

        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runtime: DatabaseRuntime | None = None
        self._repositories: RepositoryBundle | None = None
        self._thread = threading.Thread(
            target=self._serve,
            daemon=True,
            name="vf-database-repositories",
        )
        self._thread.start()
        self._ready.wait()
        if self._startup_error is not None:
            raise DatabaseOperationError("database initialization failed") from None

    def call(self, operation: Operation[T]) -> T:
        loop = self._loop
        repositories = self._repositories
        if loop is None or repositories is None or not loop.is_running():
            raise DatabaseOperationError("database runtime is unavailable")
        future: Future[T] = asyncio.run_coroutine_threadsafe(
            operation(repositories), loop
        )
        try:
            return future.result()
        except (ValueError, DatabaseOperationError):
            raise
        except Exception:
            raise DatabaseOperationError("database operation failed") from None

    def close(self) -> None:
        loop = self._loop
        runtime = self._runtime
        if loop is None or runtime is None or not loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(runtime.close(), loop).result(timeout=5)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            self._thread.join(timeout=5)

    def _serve(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            runtime = create_database_runtime(self.settings)
            self._loop = loop
            self._runtime = runtime
            self._repositories = create_repositories(runtime)
        except BaseException as error:  # pragma: no cover - defensive startup boundary
            self._startup_error = error
            self._ready.set()
            loop.close()
            return
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()


_GATEWAYS: dict[DatabaseSettings, RepositoryGateway] = {}
_GATEWAYS_LOCK = threading.Lock()


def repository_gateway(
    settings: DatabaseSettings | None = None,
) -> RepositoryGateway:
    """Return one process-local gateway for the resolved, secret-safe settings."""

    resolved = settings or DatabaseSettings.from_env()
    with _GATEWAYS_LOCK:
        gateway = _GATEWAYS.get(resolved)
        if gateway is None:
            gateway = RepositoryGateway(resolved)
            _GATEWAYS[resolved] = gateway
        return gateway
