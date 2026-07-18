from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import URL

from app.db.settings import (
    DatabaseBackend,
    DatabaseConfigurationError,
    DatabaseSettings,
)


def test_sqlite_is_explicit_default_and_ignores_legacy_database_url() -> None:
    settings = DatabaseSettings.from_env(
        {"DATABASE_URL": URL.create("postgresql", host="must-not-be-read.invalid").render_as_string()}
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
            "DATABASE_URL": URL.create("postgresql", host="wrong.invalid").render_as_string(),
            "SUPABASE_DB_URL": URL.create(
                "postgresql",
                username="vf",
                password=secret,
                host="db.example.test",
                database="verifierforge",
            ).render_as_string(hide_password=False),
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
        {"VF_DB_BACKEND": "postgres", "SUPABASE_DB_URL": "sqlite:" + "///wrong"},
        {"VF_DB_BACKEND": "other"},
    ],
)
def test_configuration_errors_are_sanitized(environ: dict[str, str]) -> None:
    with pytest.raises(DatabaseConfigurationError) as captured:
        DatabaseSettings.from_env(environ)

    assert "not a url" not in str(captured.value)
    assert ("sqlite:" + "///wrong") not in str(captured.value)


def test_postgres_pool_settings_are_bounded_and_secret_safe() -> None:
    url = URL.create(
        "postgresql",
        username="vf",
        password="fixture",
        host="db.example.test",
        database="verifierforge",
    ).render_as_string(hide_password=False)
    settings = DatabaseSettings.from_env(
        {
            "VF_DB_BACKEND": "postgres",
            "SUPABASE_DB_URL": url,
            "VF_DB_POOL_SIZE": "7",
            "VF_DB_MAX_OVERFLOW": "3",
            "VF_DB_POOL_TIMEOUT_SECONDS": "12",
            "VF_DB_CONNECT_TIMEOUT_SECONDS": "9",
        }
    )

    assert (
        settings.pool_size,
        settings.max_overflow,
        settings.pool_timeout_seconds,
        settings.connect_timeout_seconds,
    ) == (7, 3, 12, 9)

    with pytest.raises(DatabaseConfigurationError) as captured:
        DatabaseSettings.from_env(
            {
                "VF_DB_BACKEND": "postgres",
                "SUPABASE_DB_URL": url,
                "VF_DB_POOL_SIZE": "fixture-not-an-int",
            }
        )
    assert "fixture-not-an-int" not in str(captured.value)
