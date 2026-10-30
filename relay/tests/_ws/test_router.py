import pytest
from fastapi.testclient import TestClient

from main import create_app

AUTH_QS = "token=test-session-token-0123456789abcdef0123456789abcdef"
BAD_AUTH_QS = "token=wrong-token"


def _assert_closed_with(ws, code: int) -> None:
    message = ws.receive()
    assert message["type"] == "websocket.close"
    assert message["code"] == code


@pytest.fixture
def client(write_config, config_dict) -> TestClient:
    path = write_config(config_dict)
    app = create_app(path)
    return TestClient(app)


def test_ws_thin_inventory_accepts_with_good_credentials(client: TestClient):
    with client.websocket_connect(f"/ws?{AUTH_QS}"):
        pass


def test_ws_thin_inventory_closes_4401_with_bad_credentials(client: TestClient):
    with client.websocket_connect(f"/ws?{BAD_AUTH_QS}") as ws:
        _assert_closed_with(ws, 4401)


def test_ws_thin_inventory_closes_4401_with_no_credentials(client: TestClient):
    with client.websocket_connect("/ws") as ws:
        _assert_closed_with(ws, 4401)


def test_ws_machine_closes_4401_before_checking_machine_id(client: TestClient):
    with client.websocket_connect(f"/ws/machine/999?{BAD_AUTH_QS}") as ws:
        _assert_closed_with(ws, 4401)


def test_ws_machine_closes_4404_for_unknown_machine_id(client: TestClient):
    with client.websocket_connect(f"/ws/machine/999?{AUTH_QS}") as ws:
        _assert_closed_with(ws, 4404)


def test_ws_machine_closes_4410_for_known_machine_not_yet_online(client: TestClient):
    with client.websocket_connect(f"/ws/machine/1?{AUTH_QS}") as ws:
        _assert_closed_with(ws, 4410)


def test_ws_session_token_in_the_query_is_accepted_and_serves_a_snapshot(client: TestClient):
    with client.websocket_connect(f"/ws?{AUTH_QS}") as ws:
        ws.send_json({"type": "hello", "device": "d", "clientId": "c"})
        assert ws.receive_json()["type"] == "snapshot"


def test_ws_session_token_in_the_authorization_header_is_accepted(client: TestClient):
    with client.websocket_connect("/ws", headers={"authorization": "Bearer test-session-token-0123456789abcdef0123456789abcdef"}) as ws:
        ws.send_json({"type": "hello", "device": "d", "clientId": "c"})
        assert ws.receive_json()["type"] == "snapshot"


@pytest.mark.parametrize("query,headers", [
    ("clientId=test_web_id&clientSecret=test_web_secret", {}),
    ("", {"x-client-id": "test_web_id", "x-client-secret": "test_web_secret"}),
    ("clientId=test_app_id&clientSecret=test_app_secret", {}),
])
def test_ws_client_credentials_alone_no_longer_grant_access(client: TestClient, query, headers):
    for path in ("/ws", "/ws/machine/1"):
        with client.websocket_connect(f"{path}?{query}", headers=headers) as ws:
            _assert_closed_with(ws, 4401)


def test_ws_p121_identical_close_reason_for_any_auth_failure_mode(client: TestClient):
    for query in ("token=unknown-token", "", "token="):
        with client.websocket_connect(f"/ws?{query}") as ws:
            message = ws.receive()
            assert message["code"] == 4401 and message["reason"] == "unauthorized"


def test_ws_p43_p44_real_hello_then_snapshot(client: TestClient):
    with client.websocket_connect(f"/ws?{AUTH_QS}") as ws:
        ws.send_json({"type": "hello", "device": "wall-1", "clientId": "c1"})
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert snapshot["seq"] == 1
        assert "machines" in snapshot
        assert snapshot["machines"][0]["machine_id"] == 1
        assert "servers" not in snapshot
        assert "platforms" not in snapshot
        assert "activeAlerts" in snapshot
        assert snapshot["source"] == "live"


def test_ws_p105_a_bad_hello_gets_no_snapshot(client: TestClient):
    with client.websocket_connect(f"/ws?{AUTH_QS}") as ws:
        ws.send_json({"type": "hello"})
        ws.send_json({"type": "hello", "device": "wall-1", "clientId": "c1"})
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert snapshot["seq"] == 1


def test_ws_p45_resync_returns_a_fresh_snapshot_at_the_same_seq(client: TestClient):
    with client.websocket_connect(f"/ws?{AUTH_QS}") as ws:
        ws.send_json({"type": "hello", "device": "wall-1", "clientId": "c1"})
        first = ws.receive_json()
        ws.send_json({"type": "resync"})
        second = ws.receive_json()
        assert second["type"] == "snapshot"
        assert second["seq"] == first["seq"]


def test_ws_closes_4403_for_a_public_internet_origin_not_on_the_allow_list(client: TestClient):
    with client.websocket_connect(f"/ws?{AUTH_QS}", headers={"origin": "https://evil.example.com"}) as ws:
        _assert_closed_with(ws, 4403)


def test_ws_accepts_a_lan_origin_automatically_with_no_explicit_allow_list(client: TestClient):
    with client.websocket_connect(f"/ws?{AUTH_QS}", headers={"origin": "http://192.168.1.50:19006"}):
        pass


def test_ws_accepts_a_loopback_origin_automatically(client: TestClient):
    with client.websocket_connect(f"/ws?{AUTH_QS}", headers={"origin": "http://127.0.0.1:5173"}):
        pass


def test_ws_accepts_no_origin_header_at_all(client: TestClient):
    with client.websocket_connect(f"/ws?{AUTH_QS}"):
        pass


def test_ws_accepts_a_public_origin_that_is_explicitly_configured(write_config, config_dict):
    config_dict["settings"]["ws_allowed_origins"] = ["https://dashboard.example.com"]
    path = write_config(config_dict)
    from main import create_app

    app = create_app(path)
    client_with_allowlist = TestClient(app)
    with client_with_allowlist.websocket_connect(f"/ws?{AUTH_QS}", headers={"origin": "https://dashboard.example.com"}):
        pass


def test_ws_origin_check_runs_before_auth_a_forbidden_origin_with_good_credentials_still_closes_4403(client: TestClient):
    with client.websocket_connect(f"/ws?{AUTH_QS}", headers={"origin": "https://evil.example.com"}) as ws:
        _assert_closed_with(ws, 4403)


def test_ws_machine_route_also_enforces_the_origin_check(client: TestClient):
    with client.websocket_connect(f"/ws/machine/1?{AUTH_QS}", headers={"origin": "https://evil.example.com"}) as ws:
        _assert_closed_with(ws, 4403)
