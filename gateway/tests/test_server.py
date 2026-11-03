from __future__ import annotations

import json
import logging

import aiohttp
import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer
from yarl import URL

import server


class Stub:
    def __init__(self):
        self.hits: list[str] = []
        self.ws_close_codes: list[int] = []
        self.ws_headers: list[dict] = []


def stub_app(stub: Stub) -> web.Application:
    app = web.Application()

    async def any_path(request):
        stub.hits.append(f"{request.method} {request.path}")
        if request.path == "/health":
            return web.json_response({"status": "ok"})
        if request.path == "/api/echo":
            body = await request.read()
            return web.json_response({
                "headers": dict(request.headers), "query": request.query_string, "method": request.method,
                "body": body.decode(),
            })
        if request.path.startswith("/api/"):
            return web.json_response({"detail": "Not Found"}, status=404)
        return web.Response(text="relay html", content_type="text/html")

    async def ws(request):
        stub.hits.append(f"WS {request.path}")
        stub.ws_headers.append(dict(request.headers))
        if request.path == "/ws/nope":
            raise web.HTTPNotFound()
        sock = web.WebSocketResponse()
        await sock.prepare(request)
        if "close" in request.query:
            await sock.close(code=int(request.query["close"]))
            return sock
        async for msg in sock:
            if msg.type == WSMsgType.TEXT:
                await sock.send_str("echo:" + msg.data)
            elif msg.type == WSMsgType.BINARY:
                await sock.send_bytes(msg.data[::-1])
        stub.ws_close_codes.append(sock.close_code)
        return sock

    app.router.add_get("/ws", ws)
    app.router.add_get("/ws/{tail:.*}", ws)
    app.router.add_route("*", "/{tail:.*}", any_path)
    return app


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "dashboard"
    (root / "_expo/static/js").mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "index.html").write_text("<html>app</html>")
    (root / "firebase-messaging-sw.js").write_text("//sw")
    (root / "favicon.png").write_bytes(b"png")
    (root / "_expo/static/js/app-abc.js").write_text("//js")
    (root / ".env").write_text("SECRET=1")
    (tmp_path / "secret.txt").write_text("outside")
    return root


@pytest.fixture
async def stack(site):
    stub = Stub()
    relay = TestServer(stub_app(stub))
    await relay.start_server()
    gw = TestServer(server.make_app(site, f"127.0.0.1:{relay.port}"))
    await gw.start_server()
    async with aiohttp.ClientSession() as client:
        yield stub, gw, client
    await gw.close()
    await relay.close()


@pytest.fixture
async def down_stack(site):
    gw = TestServer(server.make_app(site, "127.0.0.1:1"))  # nothing listens on port 1
    await gw.start_server()
    async with aiohttp.ClientSession() as client:
        yield gw, client
    await gw.close()


def url(gw, path):
    return URL(f"http://127.0.0.1:{gw.port}{path}", encoded=True)


async def test_index_and_spa_fallback(stack):
    stub, gw, c = stack
    for path in ["/", "/alerts", "/scheduler/new", "/pipeline/4001:7", "/pipeline/4001%3A7"]:
        async with c.get(url(gw, path)) as r:
            assert r.status == 200, path
            assert "app" in await r.text()
            assert r.headers["Cache-Control"] == "no-cache"
    assert stub.hits == []


async def test_cache_headers_and_files(stack):
    _, gw, c = stack
    async with c.get(url(gw, "/_expo/static/js/app-abc.js")) as r:
        assert r.status == 200
        assert r.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    async with c.get(url(gw, "/firebase-messaging-sw.js")) as r:
        assert r.headers["Cache-Control"] == "no-cache"
    async with c.get(url(gw, "/index.html")) as r:
        assert r.headers["Cache-Control"] == "no-cache"
    async with c.get(url(gw, "/favicon.png")) as r:
        assert r.status == 200 and r.headers["Cache-Control"] == "public, max-age=86400"
    async with c.head(url(gw, "/")) as r:
        assert r.status == 200


async def test_missing_files_are_404_not_html(stack):
    _, gw, c = stack
    for path in ["/nope.js", "/_expo/static/js/missing.js", "/assets/x/y.png", "/openapi.json"]:
        async with c.get(url(gw, path)) as r:
            assert r.status == 404, path


@pytest.mark.parametrize("path", [
    "/../secret.txt", "/%2e%2e/secret.txt", "/%2e%2e%2fsecret.txt", "/assets/../../secret.txt",
    "/..%5csecret.txt", "/.env", "/assets/%2e%2e/%2e%2e/secret.txt", "/%00", "/_expo/..%2f..%2fsecret.txt",
])
async def test_path_traversal_blocked(stack, path):
    stub, gw, c = stack
    async with c.get(url(gw, path)) as r:
        body = await r.text()
        assert "outside" not in body and "SECRET" not in body
        assert r.status in (400, 404) or "app" in body  # fallback page is fine, secrets are not
    assert stub.hits == []


async def test_relay_only_paths_never_reach_relay(stack):
    stub, gw, c = stack
    for method, path in [("GET", "/docs"), ("GET", "/redoc"), ("GET", "/openapi.json"), ("GET", "/health"),
                         ("POST", "/ops/silence"), ("GET", "/ops/silences"), ("DELETE", "/ops/silence/1"),
                         ("POST", "/config/reload"), ("GET", "/apix"), ("GET", "/wsx"), ("GET", "/api2/x")]:
        async with c.request(method, url(gw, path)) as r:
            assert "relay html" not in await r.text()
            assert r.status in (200, 404, 405), (method, path, r.status)
    assert stub.hits == []


async def test_rest_proxy_headers_and_query(stack):
    stub, gw, c = stack
    headers = {"Authorization": "Bearer tok123", "Origin": "https://mydomain.com", "X-Forwarded-For": "1.2.3.4, 5.6.7.8:4711",
               "X-Real-IP": "9.9.9.9", "Forwarded": "for=9.9.9.9"}
    async with c.post(url(gw, "/api/echo?a=1&b=x"), headers=headers, data=b"hello") as r:
        data = await r.json()
    h = {k.lower(): v for k, v in data["headers"].items()}
    assert data["query"] == "a=1&b=x" and data["method"] == "POST" and data["body"] == "hello"
    assert h["authorization"] == "Bearer tok123"
    assert h["origin"] == "https://mydomain.com"
    assert h["x-forwarded-for"] == "5.6.7.8"
    assert "x-real-ip" not in h and "forwarded" not in h


async def test_forwarded_for_fallbacks(stack):
    _, gw, c = stack
    async with c.get(url(gw, "/api/echo")) as r:
        h = {k.lower(): v for k, v in (await r.json())["headers"].items()}
    assert h["x-forwarded-for"] == "127.0.0.1"
    async with c.get(url(gw, "/api/echo"), headers={"X-Forwarded-For": "not-an-ip"}) as r:
        h = {k.lower(): v for k, v in (await r.json())["headers"].items()}
    assert h["x-forwarded-for"] == "127.0.0.1"
    async with c.get(url(gw, "/api/echo"), headers={"X-Forwarded-For": "[2001:db8::1]:55"}) as r:
        h = {k.lower(): v for k, v in (await r.json())["headers"].items()}
    assert h["x-forwarded-for"] == "2001:db8::1"


async def test_api_health_maps_to_relay_health_and_unknown_api_is_json_404(stack):
    stub, gw, c = stack
    async with c.get(url(gw, "/api/health")) as r:
        assert r.status == 200 and (await r.json()) == {"status": "ok"}
    assert stub.hits == ["GET /health"]
    async with c.get(url(gw, "/api/does-not-exist")) as r:
        assert r.status == 404 and r.content_type == "application/json"


async def test_websocket_proxy_both_directions(stack):
    stub, gw, c = stack
    async with c.ws_connect(f"ws://127.0.0.1:{gw.port}/ws?token=secrettoken", origin="https://mydomain.com") as ws:
        await ws.send_str("hi")
        assert (await ws.receive()).data == "echo:hi"
        await ws.send_bytes(b"abc")
        assert (await ws.receive()).data == b"cba"
        await ws.close(code=1000)
    h = {k.lower(): v for k, v in stub.ws_headers[0].items()}
    assert h["origin"] == "https://mydomain.com"
    async with c.ws_connect(f"ws://127.0.0.1:{gw.port}/ws/machine/7?token=t") as ws:
        await ws.send_str("m")
        assert (await ws.receive()).data == "echo:m"
    assert "WS /ws/machine/7" in stub.hits


@pytest.mark.parametrize("code", [4401, 4403, 4404, 4410, 1012])
async def test_relay_close_codes_preserved(stack, code):
    _, gw, c = stack
    async with c.ws_connect(f"ws://127.0.0.1:{gw.port}/ws?close={code}") as ws:
        msg = await ws.receive()
        assert msg.type == WSMsgType.CLOSE and msg.data == code
        assert ws.close_code == code


async def test_client_close_code_reaches_relay(stack):
    stub, gw, c = stack
    ws = await c.ws_connect(f"ws://127.0.0.1:{gw.port}/ws")
    await ws.send_str("x")
    await ws.receive()
    await ws.close(code=4000)
    for _ in range(50):
        if stub.ws_close_codes:
            break
        await __import__("asyncio").sleep(0.05)
    assert stub.ws_close_codes == [4000]


async def test_websocket_refused_by_relay_and_plain_get(stack):
    _, gw, c = stack
    with pytest.raises(aiohttp.WSServerHandshakeError) as exc:
        await c.ws_connect(f"ws://127.0.0.1:{gw.port}/ws/nope")
    assert exc.value.status == 404
    async with c.get(url(gw, "/ws")) as r:
        assert r.status == 426


async def test_relay_down_dashboard_loads_api_fails(down_stack):
    gw, c = down_stack
    async with c.get(url(gw, "/")) as r:
        assert r.status == 200
    for path in ["/api/health", "/api/config/check"]:
        async with c.get(url(gw, path)) as r:
            assert r.status == 502 and (await r.json())["detail"] == "relay_unavailable"
    with pytest.raises(aiohttp.WSServerHandshakeError) as exc:
        await c.ws_connect(f"ws://127.0.0.1:{gw.port}/ws")
    assert exc.value.status == 502


async def test_dashboard_not_deployed_is_503(tmp_path):
    gw = TestServer(server.make_app(tmp_path / "missing", "127.0.0.1:1"))
    await gw.start_server()
    async with aiohttp.ClientSession() as c:
        async with c.get(url(gw, "/")) as r:
            assert r.status == 503
    await gw.close()


async def test_query_string_never_logged(stack, caplog):
    _, gw, c = stack
    caplog.set_level(logging.DEBUG)
    async with c.get(url(gw, "/api/echo?token=SUPERSECRET")) as r:
        await r.read()
    async with c.ws_connect(f"ws://127.0.0.1:{gw.port}/ws?token=SUPERSECRET") as ws:
        await ws.close()
    # the stub relay and aiohttp's test server log access lines themselves; the gateway runs with access_log=None
    own = " | ".join(r.getMessage() for r in caplog.records if r.name == "gateway")
    assert "SUPERSECRET" not in own
    assert "/api/echo" in own and "/ws" in own
