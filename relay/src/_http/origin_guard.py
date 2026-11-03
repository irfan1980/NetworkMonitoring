from __future__ import annotations

import json

from starlette.types import ASGIApp, Receive, Scope, Send

from _lib.origin_check import is_origin_allowed

ALLOWED_METHODS = "GET, POST, PUT, DELETE, OPTIONS"
ALLOWED_HEADERS = "authorization, content-type, x-ack-pin"
PREFLIGHT_MAX_AGE = "600"
ORIGIN_FORBIDDEN_BODY = json.dumps({"detail": "origin_forbidden"}).encode()


class OriginGuardMiddleware:
    """Applies the WebSocket origin rule to HTTP: refuses a disallowed Origin with 403, answers
    preflights, and adds CORS headers only for allowed origins. A request with no Origin passes untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        origin = None
        for key, value in scope["headers"]:
            if key == b"origin":
                origin = value.decode("latin-1")
                break
        if not origin:
            await self.app(scope, receive, send)
            return

        config_store = scope["app"].state.config_store
        if not is_origin_allowed(origin, config_store.get().settings.ws_allowed_origins):
            await self._respond(send, 403, ORIGIN_FORBIDDEN_BODY, [(b"content-type", b"application/json")])
            return

        cors = [
            (b"access-control-allow-origin", origin.encode("latin-1")),
            (b"vary", b"Origin"),
        ]
        is_preflight = scope["method"] == "OPTIONS" and any(
            k == b"access-control-request-method" for k, _ in scope["headers"]
        )
        if is_preflight:
            cors += [
                (b"access-control-allow-methods", ALLOWED_METHODS.encode()),
                (b"access-control-allow-headers", ALLOWED_HEADERS.encode()),
                (b"access-control-max-age", PREFLIGHT_MAX_AGE.encode()),
            ]
            await self._respond(send, 204, b"", cors)
            return

        async def send_with_cors(message) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *cors]}
            await send(message)

        await self.app(scope, receive, send_with_cors)

    @staticmethod
    async def _respond(send: Send, status: int, body: bytes, headers: list[tuple[bytes, bytes]]) -> None:
        headers = [*headers, (b"content-length", str(len(body)).encode())]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
