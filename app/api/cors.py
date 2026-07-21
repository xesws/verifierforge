"""Environment-controlled CORS defaults for local frontend development."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


DEFAULT_LOCAL_ORIGINS = tuple(
    f"http://{host}:{port}"
    for host in ("localhost", "127.0.0.1")
    for port in (3000, 5173, 8080)
)


def cors_origins(environ: Mapping[str, str] | None = None) -> list[str]:
    """Resolve an explicit allowlist, defaulting only to common local origins."""

    values = os.environ if environ is None else environ
    raw = values.get("VF_CORS_ORIGINS", "").strip()
    if not raw:
        return list(DEFAULT_LOCAL_ORIGINS)
    if raw == "*":
        return ["*"]

    origins: list[str] = []
    for item in raw.split(","):
        origin = item.strip().rstrip("/")
        if origin and origin not in origins:
            origins.append(origin)
    if "*" in origins:
        raise ValueError("VF_CORS_ORIGINS wildcard must be the only value")
    return origins or list(DEFAULT_LOCAL_ORIGINS)


def cors_origin_regex(environ: Mapping[str, str] | None = None) -> str | None:
    """Resolve and validate an optional hosted-preview origin expression."""

    values = os.environ if environ is None else environ
    raw = values.get("VF_CORS_ORIGIN_REGEX", "").strip()
    if not raw:
        return None
    try:
        re.compile(raw)
    except re.error:
        raise ValueError("VF_CORS_ORIGIN_REGEX must be a valid regular expression") from None
    return raw


def configure_cors(
    app: FastAPI, environ: Mapping[str, str] | None = None
) -> None:
    """Install the shared non-credentialed API CORS policy."""

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(environ),
        allow_origin_regex=cors_origin_regex(environ),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-VerifierForge-Route"],
    )


__all__ = [
    "DEFAULT_LOCAL_ORIGINS",
    "configure_cors",
    "cors_origin_regex",
    "cors_origins",
]
