from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import HTTPException, Request

REJECTION_REASON = "unauthorized"
SESSION_INVALID_DETAIL = "session_invalid"

logger = logging.getLogger("relay.auth")


def timing_safe_equal_str(a: str, b: str) -> bool:
    buf_a = a.encode("utf-8")
    buf_b = b.encode("utf-8")
    if len(buf_a) != len(buf_b):
        hmac.compare_digest(buf_a, buf_a)  # pay the same cost; result deliberately discarded
        return False
    return hmac.compare_digest(buf_a, buf_b)


def bearer_token(headers, query_params) -> tuple[Optional[str], str]:
    header = headers.get("authorization") or ""
    if header.lower().startswith("bearer ") and header[7:].strip():
        return header[7:].strip(), "header"
    token = query_params.get("token")
    return (token, "query") if token else (None, "none")


def authenticate(app_state, token: Optional[str]):
    return app_state.sessions.verify(token)


async def require_session(request: Request):
    token, source = bearer_token(request.headers, request.query_params)
    session, reason = authenticate(request.app.state, token)
    if session is None:
        logger.warning(
            "session rejected: %s (token from %s) path=%s client_ip=%s",
            reason, source, request.url.path, request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=401, detail=SESSION_INVALID_DETAIL)
    request.state.session = session
