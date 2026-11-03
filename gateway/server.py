"""Public gateway: dashboard static files plus an allowlisted reverse proxy to the relay.

Forwards only /api/* (with /api/health mapped to the relay's /health) and /ws, /ws/*.
Everything else is static files with an index.html fallback and never reaches the relay.
Query strings carry session tokens, so they are never logged.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import aiohttp
from aiohttp import WSMsgType, web

log = logging.getLogger("gateway")
CFG = web.AppKey("cfg", dict)
SESSION = web.AppKey("session", aiohttp.ClientSession)

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "transfer-encoding", "upgrade", "content-length", "host",
}
STRIP_FORWARDING = {"x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-port", "forwarded", "x-real-ip"}
WS_HANDSHAKE = {"sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions", "sec-websocket-protocol", "sec-websocket-accept"}
NO_CACHE = "no-cache"
IMMUTABLE = "public, max-age=31536000, immutable"
ASSET_CACHE = "public, max-age=86400"


def _strip_port(value: str) -> Optional[str]:
    if not value:
        return None
    if value.startswith("["):
        value = value[1:].split("]", 1)[0]
    elif value.count(":") == 1:
        value = value.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def client_ip(request: web.Request) -> str:
    # App Service's front end appends the address it saw as the last X-Forwarded-For entry (possibly ip:port).
    # Earlier entries are client-controlled and are ignored.
    raw = request.headers.get("X-Forwarded-For", "")
    last = raw.split(",")[-1].strip() if raw else ""
    candidate = _strip_port(last)
    if candidate:
        return candidate
    peer = request.transport.get_extra_info("peername") if request.transport else None
    return peer[0] if peer else "0.0.0.0"


def upstream_headers(request: web.Request, *, drop_ws: bool = False) -> dict:
    out = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in HOP_BY_HOP or lk in STRIP_FORWARDING or (drop_ws and lk in WS_HANDSHAKE):
            continue
        out[k] = v
    out["X-Forwarded-For"] = client_ip(request)
    https = request.headers.get("X-ARR-SSL") or request.headers.get("X-Forwarded-Proto", "").lower() == "https"
    out["X-Forwarded-Proto"] = "https" if https else request.scheme
    if request.host:
        out["X-Forwarded-Host"] = request.host
    return out


def json_error(status: int, detail: str) -> web.Response:
    return web.json_response({"detail": detail}, status=status, headers={"Cache-Control": NO_CACHE})


def upstream_path(path: str) -> Optional[str]:
    if path == "/api/health":
        return "/health"
    if path == "/api" or path.startswith("/api/"):
        return path
    return None


def is_ws_path(path: str) -> bool:
    return path == "/ws" or path.startswith("/ws/")


async def proxy_http(request: web.Request, target: str) -> web.StreamResponse:
    cfg = request.app[CFG]
    url = f"http://{cfg['upstream']}{target}"
    if request.query_string and target == request.path:
        url += "?" + request.query_string
    session: aiohttp.ClientSession = request.app[SESSION]
    body = request.content if request.can_read_body else None
    try:
        async with session.request(request.method, url, headers=upstream_headers(request), data=body,
                                   allow_redirects=False) as upstream:
            resp = web.StreamResponse(status=upstream.status, reason=upstream.reason)
            for k, v in upstream.headers.items():
                if k.lower() not in HOP_BY_HOP:
                    resp.headers.add(k, v)
            await resp.prepare(request)
            async for chunk in upstream.content.iter_any():
                await resp.write(chunk)
            await resp.write_eof()
            return resp
    except aiohttp.ClientConnectorError:
        return json_error(502, "relay_unavailable")
    except asyncio.TimeoutError:
        return json_error(504, "relay_timeout")
    except aiohttp.ClientError as exc:
        log.warning("upstream error %s on %s %s", type(exc).__name__, request.method, request.path)
        return json_error(502, "relay_unavailable")


async def proxy_ws(request: web.Request) -> web.StreamResponse:
    if request.headers.get("Upgrade", "").lower() != "websocket":
        return json_error(426, "websocket_required")
    cfg = request.app[CFG]
    url = f"ws://{cfg['upstream']}{request.path}"
    if request.query_string:
        url += "?" + request.query_string
    protocols = [p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",") if p.strip()]
    session: aiohttp.ClientSession = request.app[SESSION]
    try:
        up = await session.ws_connect(url, headers=upstream_headers(request, drop_ws=True), protocols=protocols,
                                      autoclose=False, autoping=False, max_msg_size=0, heartbeat=None,
                                      timeout=aiohttp.ClientWSTimeout(ws_close=5))
    except aiohttp.WSServerHandshakeError as exc:
        # the relay refused the upgrade (unknown path, origin ...): give the client the same status
        return json_error(exc.status if 400 <= exc.status < 600 else 502, "relay_refused_websocket")
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return json_error(502, "relay_unavailable")

    down = web.WebSocketResponse(autoclose=False, autoping=False, max_msg_size=0,
                                 protocols=[up.protocol] if up.protocol else ())
    await down.prepare(request)

    async def pump(src, dst):
        async for msg in src:
            if msg.type == WSMsgType.TEXT:
                await dst.send_str(msg.data)
            elif msg.type == WSMsgType.BINARY:
                await dst.send_bytes(msg.data)
            elif msg.type == WSMsgType.PING:
                await dst.ping(msg.data)
            elif msg.type == WSMsgType.PONG:
                await dst.pong(msg.data)
            elif msg.type == WSMsgType.ERROR:
                break
        return src.close_code

    t_up = asyncio.ensure_future(pump(up, down))
    t_down = asyncio.ensure_future(pump(down, up))
    done, pending = await asyncio.wait({t_up, t_down}, return_when=asyncio.FIRST_COMPLETED)
    first = next(iter(done))
    from_relay = first is t_up
    try:
        code = first.result()
    except Exception:
        code = None
    if code is None or code == 1005:
        # no close frame was received: the peer vanished
        code = 1011 if from_relay else 1001
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    # pass the close on to the side that did not start it, keeping the code, then answer the starter
    for ws in ((down, up) if from_relay else (up, down)):
        try:
            if not ws.closed:
                await ws.close(code=code)
        except Exception:
            pass
    return down


def safe_file(root: Path, rel: str) -> Optional[Path]:
    if "\x00" in rel or "\\" in rel:
        return None
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." or p.startswith(".") for p in parts):
        return None
    try:
        resolved = root.joinpath(*parts).resolve() if parts else root.resolve()
        resolved.relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return resolved


def cache_for(rel: str) -> str:
    if rel.startswith("_expo/static/"):
        return IMMUTABLE
    if rel in ("index.html", "firebase-messaging-sw.js"):
        return NO_CACHE
    return ASSET_CACHE


async def serve_static(request: web.Request, rel: str) -> web.StreamResponse:
    if request.method not in ("GET", "HEAD"):
        return web.Response(status=405, headers={"Allow": "GET, HEAD"})
    root: Path = request.app[CFG]["static_dir"]
    target = safe_file(root, rel)
    if target is None:
        return web.Response(status=404, text="not found", headers={"Cache-Control": NO_CACHE})
    headers = {"X-Content-Type-Options": "nosniff"}
    if target.is_file():
        headers["Cache-Control"] = cache_for(rel)
        return web.FileResponse(target, headers=headers)
    index = root / "index.html"
    last = rel.rsplit("/", 1)[-1]
    if "." in last or rel.startswith(("_expo/", "assets/")):
        return web.Response(status=404, text="not found", headers={"Cache-Control": NO_CACHE})
    if not index.is_file():
        return web.Response(status=503, text="dashboard not deployed", headers={"Cache-Control": NO_CACHE})
    headers["Cache-Control"] = NO_CACHE
    return web.FileResponse(index, headers=headers)


async def handle(request: web.Request) -> web.StreamResponse:
    path = request.path
    if is_ws_path(path):
        return await proxy_ws(request)
    target = upstream_path(path)
    if target is not None:
        return await proxy_http(request, target)
    return await serve_static(request, path.lstrip("/"))


@web.middleware
async def access_log(request: web.Request, handler):
    status = 500
    try:
        resp = await handler(request)
        status = resp.status
        return resp
    except web.HTTPException as exc:
        status = exc.status
        raise
    finally:
        log.info("%s %s %s", request.method, request.path, status)  # path only, never the query string


def make_app(static_dir: Path, upstream: str = "127.0.0.1:8085") -> web.Application:
    app = web.Application(middlewares=[access_log], client_max_size=16 * 1024 * 1024)
    app[CFG] = {"static_dir": Path(static_dir), "upstream": upstream}

    async def on_startup(app):
        app[SESSION] = aiohttp.ClientSession(
            auto_decompress=False, cookie_jar=aiohttp.DummyCookieJar(),
            timeout=aiohttp.ClientTimeout(total=90, connect=3, sock_connect=3),
        )

    async def on_cleanup(app):
        await app[SESSION].close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_route("*", "/{tail:.*}", handle)
    return app


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stdout)
    port = int(os.environ.get("GATEWAY_PORT") or os.environ.get("WEBSITES_PORT") or 8000)
    host = os.environ.get("GATEWAY_HOST", "0.0.0.0")
    static_dir = Path(os.environ.get("GATEWAY_STATIC_DIR", "/home/site/wwwroot/dashboard"))
    upstream = os.environ.get("RELAY_UPSTREAM", "127.0.0.1:8085")
    app = make_app(static_dir, upstream)
    log.info("gateway listening on %s:%s, static %s, relay %s", host, port, static_dir, upstream)
    web.run_app(app, host=host, port=port, access_log=None, print=None, shutdown_timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
