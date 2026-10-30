from fastapi.testclient import TestClient

from _fixtures import env
from main import create_app

AUTH_QS = "token=test-session-token-0123456789abcdef0123456789abcdef"


_OFFLINE_CREDS = {"host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "db", "dbname": "db"}


def _mysql_node(offline: bool = False):
    c = _OFFLINE_CREDS if offline else env.mysql_credentials()
    return {
        "node_id": "node-mysql-1001", "node_name": "MySQL", "node_type": "Database", "node_platform": "mysql",
        "server_id": 1001,
        "node_config": {
            "host": c["host"], "port": c["port"], "user": c["user"], "password": c["password"],
            "database": c["database"],
        },
    }


def _postgresql_node(offline: bool = False):
    c = _OFFLINE_CREDS if offline else env.postgresql_credentials()
    return {
        "node_id": "node-postgres-1002", "node_name": "PostgreSQL", "node_type": "Database",
        "node_platform": "postgresql", "server_id": 1002,
        "node_config": {
            "host": c["host"], "port": c["port"], "user": c["user"], "password": c["password"],
            "database": c["dbname"], "schema": "sync",
        },
    }


def _client(write_config, config_dict, offline: bool = False, standalone: bool = False):
    nodes = [_mysql_node(offline), _postgresql_node(offline)]
    if standalone:
        config_dict["machines"][0]["nodes"] = []
        config_dict["services"] = nodes
    else:
        config_dict["machines"][0]["nodes"] = nodes
    path = write_config(config_dict)
    return TestClient(create_app(path))


def test_pair_entities_requires_auth(write_config, config_dict):
    client = _client(write_config, config_dict, offline=True)
    resp = client.get("/api/pairs/1001/1002/entities")
    assert resp.status_code == 401


def test_server_entities_requires_auth(write_config, config_dict):
    client = _client(write_config, config_dict, offline=True)
    resp = client.get("/api/servers/1001/entities")
    assert resp.status_code == 401


def test_platform_entities_alias_requires_auth(write_config, config_dict):
    client = _client(write_config, config_dict, offline=True)
    resp = client.get("/api/platforms/1001/entities")
    assert resp.status_code == 401


def test_pair_entities_404_on_unknown_server(write_config, config_dict):
    client = _client(write_config, config_dict, offline=True)
    resp = client.get(f"/api/pairs/1001/9999/entities?{AUTH_QS}")
    assert resp.status_code == 404


def test_pair_entities_real_stable_registry_shape_without_any_seeded_row(write_config, config_dict):
    client = _client(write_config, config_dict)
    resp = client.get(f"/api/pairs/1001/1002/entities?{AUTH_QS}")
    assert resp.status_code == 200
    body = resp.json()
    order = next(e for e in body["entities"] if e["entity"] == "ORDER")
    assert order["senderFunction"] is None
    assert order["receiverProcedure"] == "createOrder"
    assert order["receiverFormat"] == "snake_case"
    assert order["severity"] == 2


def test_pair_entities_reflects_a_real_seeded_matched_pair_fix01(write_config, config_dict, mysql_scenario, postgresql_scenario):
    mysql_scenario("FIX01")
    client = _client(write_config, config_dict)

    resp = client.get(f"/api/pairs/1001/1002/entities?{AUTH_QS}")
    assert resp.status_code == 200
    order = next(e for e in resp.json()["entities"] if e["entity"] == "ORDER")
    assert order["sentInWindow"] == 0
    assert order["appliedInWindow"] >= 1


def test_pair_entities_c7_non_default_e2e_window_seconds_narrows_the_default_window(
    write_config, config_dict, mysql_scenario
):
    mysql_scenario("FIX01")
    config_dict["settings"]["e2e_window_seconds"] = 10
    client = _client(write_config, config_dict)

    resp = client.get(f"/api/pairs/1001/1002/entities?{AUTH_QS}")
    assert resp.status_code == 200
    order = next((e for e in resp.json()["entities"] if e["entity"] == "ORDER"), None)
    assert order is None or order["sentInWindow"] == 0


def test_pair_entities_window_param_validated(write_config, config_dict):
    client = _client(write_config, config_dict, offline=True)
    resp = client.get(f"/api/pairs/1001/1002/entities?{AUTH_QS}&window=-5")
    assert resp.status_code == 400


def test_pair_entities_window_param_narrows_the_windowed_counters(write_config, config_dict, mysql_scenario):
    mysql_scenario("FIX01")
    client = _client(write_config, config_dict)

    resp = client.get(f"/api/pairs/1001/1002/entities?{AUTH_QS}&window=10")
    assert resp.status_code == 200
    order = next((e for e in resp.json()["entities"] if e["entity"] == "ORDER"), None)
    assert order is None or order["sentInWindow"] == 0


def test_server_entities_404_on_unknown_server(write_config, config_dict):
    client = _client(write_config, config_dict, offline=True)
    resp = client.get(f"/api/servers/9999/entities?{AUTH_QS}")
    assert resp.status_code == 404


def test_server_entities_groups_every_local_pipeline_real_data(write_config, config_dict):
    client = _client(write_config, config_dict)
    resp = client.get(f"/api/servers/1001/entities?{AUTH_QS}")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"platform": "1001", "outgoing": [], "incoming": []}


def test_platform_entities_alias_matches_server_entities_route(write_config, config_dict):
    client = _client(write_config, config_dict)
    a = client.get(f"/api/servers/1001/entities?{AUTH_QS}").json()
    b = client.get(f"/api/platforms/1001/entities?{AUTH_QS}").json()

    assert a == b


class _FakeDriver:
    def __init__(self, platform: str, down: bool, side: str):
        self.down, self.side = down, side

    async def _guard(self):
        if self.down:
            raise OSError("connection refused")

    async def fetch_pipelines_with_metrics(self):
        await self._guard()
        return [{
            "pipelineId": 7, "entityType": "ORDER", "sourceSystem": "1001", "targetSystem": "1002",
            "direction": "outgoing" if self.side == "from" else "incoming", "lastPingAt": "2026-10-14T00:00:00Z",
        }]

    async def fetch_outbox_entity_agg(self, ids):
        await self._guard()
        return [{"entity_type": "ORDER", "pending": 3, "sent": 1, "failed": 0}]

    async def fetch_inbox_entity_agg(self, ids):
        await self._guard()
        return [{
            "entity_type": "ORDER", "received": 2, "applied": 1, "unresolved": 1, "lastError": "boom",
            "lastErrorAt": "2026-10-14T00:01:00Z", "lastErrorMessageId": "m-1",
        }]

    async def fetch_outbox_sent(self, seconds):
        await self._guard()
        return [{"pipeline_id": 7, "sent_at": "2026-10-14T00:00:30Z"}]

    async def fetch_inbox_since(self, seconds):
        await self._guard()
        return [{"pipeline_id": 7, "status": "Applied", "applied_at": "2026-10-14T00:00:40Z"}]

    async def fetch_triggers(self):
        await self._guard()
        return [
            {"pipeline_id": 7, "operation": op, "function": "fn", "format": "snake_case", "severity": 2, "direction": "x"}
            for op in ("Update", "Insert")
        ]


def _patched_client(monkeypatch, write_config, config_dict, down_from=False, down_to=False, bus_down=False, standalone=False):
    import _http.entities as ent
    from types import SimpleNamespace
    from _metrics.readability import ReadStatus

    def build(server, settings):
        side = "from" if server.id == 1001 else "to"
        return _FakeDriver(server.platform, down_from if side == "from" else down_to, side)

    async def attribution(state):
        emitted = {"bus:1:endpoint": ReadStatus.OK}
        if bus_down:
            emitted = {"bus:1:endpoint": next(x for x in ReadStatus if x != ReadStatus.OK)}
        return SimpleNamespace(emitted=emitted, blocked_by={})

    monkeypatch.setattr(ent, "build_driver", build)
    monkeypatch.setattr(ent, "refresh_attribution", attribution)
    monkeypatch.setattr(ent, "describe_driver_error", lambda exc, platform, label: (isinstance(exc, OSError), f"{label} unreachable"))
    client = _client(write_config, config_dict, offline=True, standalone=standalone)
    cfg = client.app.state.config_store.get()
    cfg.service_busses.append(SimpleNamespace(id=1, name="bus1", topic="t"))
    for s in cfg.servers:
        s.used_buses = [1]
    return client


def test_pair_entities_bus_down_still_returns_db_data(monkeypatch, write_config, config_dict):
    client = _patched_client(monkeypatch, write_config, config_dict, bus_down=True)
    resp = client.get(f"/api/pairs/1001/1002/entities?{AUTH_QS}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bus"]["available"] is False and body["bus"]["reason"] == "bus_down"
    assert body["sides"] == {"from": {"available": True}, "to": {"available": True}}
    order = body["entities"][0]
    assert order["pending"] == 3 and order["queued"] == 2


def test_pair_entities_one_db_down_returns_partial(monkeypatch, write_config, config_dict):
    client = _patched_client(monkeypatch, write_config, config_dict, down_from=True)
    resp = client.get(f"/api/pairs/1001/1002/entities?{AUTH_QS}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sides"]["from"]["available"] is False
    assert body["sides"]["from"]["reason"] == "db_unreachable"
    assert body["sides"]["to"] == {"available": True}
    order = body["entities"][0]
    assert order["pending"] == 0 and order["queued"] == 2 and order["receiverProcedure"] == "fn"


def test_pair_entities_all_down_is_503(monkeypatch, write_config, config_dict):
    client = _patched_client(monkeypatch, write_config, config_dict, down_from=True, down_to=True, bus_down=True)
    resp = client.get(f"/api/pairs/1001/1002/entities?{AUTH_QS}")
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["error"] == "nothing_readable"
    assert detail["from"]["available"] is False and detail["to"]["available"] is False


TS_ENTITY_FIELDS = {
    "entity", "pipelineId", "senderFunction", "senderFormat", "receiverProcedure", "receiverFormat", "operations",
    "severity", "pending", "sentInWindow", "appliedInWindow", "errors", "lastSentAt", "lastAppliedAt", "lastError", "queued",
}
TS_LAST_ERROR_FIELDS = {"at", "message", "messageId"}


def _assert_entity_contract(e: dict) -> None:
    assert set(e) == TS_ENTITY_FIELDS
    assert isinstance(e["operations"], list)
    for k in ("pending", "sentInWindow", "appliedInWindow", "errors", "queued"):
        assert isinstance(e[k], int)
    assert e["lastError"] is None or set(e["lastError"]) == TS_LAST_ERROR_FIELDS


def test_pair_entity_row_matches_the_dashboard_contract_fixture(monkeypatch, write_config, config_dict):
    import json
    from pathlib import Path

    client = _patched_client(monkeypatch, write_config, config_dict)
    body = client.get(f"/api/pairs/1001/1002/entities?{AUTH_QS}").json()
    fixture = json.loads((Path(__file__).parent / "fixtures" / "pair_entities_response.json").read_text())
    assert body == fixture
    for e in fixture["entities"]:
        _assert_entity_contract(e)
    assert set(fixture) == {"from", "to", "window", "entities", "sides", "bus"}


def test_unavailable_side_fields_are_null_and_operations_empty(monkeypatch, write_config, config_dict):
    client = _patched_client(monkeypatch, write_config, config_dict, down_from=True)
    row = client.get(f"/api/pairs/1001/1002/entities?{AUTH_QS}").json()["entities"][0]
    _assert_entity_contract(row)
    assert row["senderFunction"] is None and row["senderFormat"] is None and row["lastSentAt"] is None
    assert row["receiverFormat"] == "snake_case"


def test_server_entities_follow_the_platform_contract(monkeypatch, write_config, config_dict):
    client = _patched_client(monkeypatch, write_config, config_dict)
    body = client.get(f"/api/servers/1001/entities?{AUTH_QS}").json()
    assert set(body) == {"platform", "outgoing", "incoming"} and body["platform"] == "1001"
    assert [g["to"] for g in body["outgoing"]] == ["1002"] and body["incoming"] == []
    for e in body["outgoing"][0]["entities"]:
        _assert_entity_contract(e)
        assert e["receiverProcedure"] is None and e["senderFunction"] == "fn"


def test_pair_entities_accepts_standalone_service_servers_with_a_bus_down(monkeypatch, write_config, config_dict):
    client = _patched_client(monkeypatch, write_config, config_dict, bus_down=True, standalone=True)
    resp = client.get(f"/api/pairs/1001/1002/entities?{AUTH_QS}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bus"]["available"] is False and body["bus"]["reason"] == "bus_down"
    assert body["sides"] == {"from": {"available": True}, "to": {"available": True}}
