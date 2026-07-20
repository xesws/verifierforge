"""One-port reviewer surface: API/UI plus OpenAI-compatible proxy."""

from __future__ import annotations

import base64
import hmac
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.main import app as api_app
from app.proxy.main import app as proxy_app


def create_app() -> FastAPI:
    invite = os.environ.get("VF_REVIEW_INVITE_CODE", "")
    if not invite:
        raise RuntimeError("VF_REVIEW_INVITE_CODE is required for the full reviewer app")

    reviewer = FastAPI(title="VerifierForge Reviewer Sandbox")

    @reviewer.middleware("http")
    async def invitation_gate(request: Request, call_next):
        if request.url.path == "/healthz":
            return JSONResponse({"status": "ok"})
        supplied = _basic_password(request.headers.get("authorization", ""))
        if supplied is None or not hmac.compare_digest(supplied, invite):
            return JSONResponse(
                {"detail": "Invitation required"},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="VerifierForge"'},
            )
        return await call_next(request)

    reviewer.mount("/proxy", proxy_app)
    reviewer.mount("/", api_app)
    return reviewer


def _basic_password(value: str) -> str | None:
    if not value.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(value.removeprefix("Basic "), validate=True).decode(
            "utf-8"
        )
    except (ValueError, UnicodeDecodeError):
        return None
    username, separator, password = decoded.partition(":")
    if not separator or username != "judge":
        return None
    return password


app = create_app()


__all__ = ["app", "create_app"]
