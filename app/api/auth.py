"""Shared invitation authentication for reviewer-only API controls."""

from __future__ import annotations

import base64
import hmac
import os

from fastapi import HTTPException, Request


def require_invitation(request: Request) -> None:
    """Require the runtime reviewer invitation without logging its value."""

    expected = os.environ.get("VF_REVIEW_INVITE_CODE", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Reviewer invitation is not configured")
    value = request.headers.get("authorization", "")
    supplied: str | None = None
    if value.startswith("Basic "):
        try:
            decoded = base64.b64decode(
                value.removeprefix("Basic "), validate=True
            ).decode()
            username, separator, password = decoded.partition(":")
            if separator and username == "judge":
                supplied = password
        except (ValueError, UnicodeDecodeError):
            supplied = None
    if supplied is None or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail="Invitation required",
            headers={"WWW-Authenticate": 'Basic realm="VerifierForge"'},
        )


__all__ = ["require_invitation"]
