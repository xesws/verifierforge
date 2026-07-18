from __future__ import annotations

from pathlib import Path

import pytest

from app.db.settings import (
    DatabaseBackend,
    DatabaseConfigurationError,
    DatabaseSettings,
)


def test_sqlite_is_explicit_default_and_ignores_legacy_database_url() -> None:
    settings = DatabaseSettings.from_env(
        {"DATABASE_URL": "postgresql://must-not-be-read.invalid/secret"}
    )

    assert settings.backend is DatabaseBackend.SQLITE
    assert settings.url.drivername == "sqlite+aiosqlite"
    assert settings.url.database == "app/proxy/traffic.db"


def test_sqlite_uses_proxy_path(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "vf.sqlite3"
    settings = DatabaseSettings.from_env(
        {"VF_DB_BACKEND": "sqlite", "VF_PROXY_DB_PATH": str(path)}
    )

    assert settings.url.database == str(path)
    assert "password" not in repr(settings)


def test_postgres_requires_only_supabase_url_and_never_renders_secret() -> None:
    secret = "do-not-render-this-password"
    settings = DatabaseSettings.from_env(
        {
            "VF_DB_BACKEND": "postgres",
            "DATABASE_URL": "postgresql://ignored:ignored@wrong.invalid/db",
            "SUPABASE_DB_URL": f"postgresql://vf:{secret}@db.example.test/verifierforge",
        }
    )

    assert settings.backend is DatabaseBackend.POSTGRES
    assert settings.url.drivername == "postgresql+asyncpg"
    assert settings.url.host == "db.example.test"
    assert secret not in repr(settings)
    assert secret not in str(settings)
    assert secret not in str(settings.url)


@pytest.mark.parametrize(
    "environ",
    [
        {"VF_DB_BACKEND": "postgres"},
        {"VF_DB_BACKEND": "postgres", "SUPABASE_DB_URL": "not a url"},
        {"VF_DB_BACKEND": "postgres", "SUPABASE_DB_URL": "sqlite:///wrong"},
        {"VF_DB_BACKEND": "other"},
    ],
)
def test_configuration_errors_are_sanitized(environ: dict[str, str]) -> None:
    with pytest.raises(DatabaseConfigurationError) as captured:
        DatabaseSettings.from_env(environ)

    assert "not a url" not in str(captured.value)
    assert "sqlite:///wrong" not in str(captured.value)
