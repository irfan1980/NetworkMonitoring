import pytest
from fastapi.testclient import TestClient

from _fixtures import connections, env, rows
from main import create_app

AUTH_QS = "token=test-session-token-0123456789abcdef0123456789abcdef"


def _mysql_node(offline: bool = False):
    c = (
        {"host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "db"}
        if offline
        else env.mysql_credentials()
    )
    return {
        "node_id": "node-mysql-1001", "node_name": "MySQL", "node_type": "Database", "node_platform": "mysql",
        "server_id": 1001,
        "node_config": {
            "host": c["host"], "port": c["port"], "user": c["user"], "password": c["password"],
            "database": c["database"],
        },
    }


def _client(write_config, config_dict, offline: bool = False):
    config_dict["machines"][0]["nodes"] = [_mysql_node(offline)]
    path = write_config(config_dict)
    return TestClient(create_app(path))


@pytest.fixture
def failed_outbox_row():
    tag = "TASK26ERR"
    entity_key = f"{tag}-1"
    mid = rows.message_id("1001", "ORDER", entity_key, 1, 101)
    with connections.mysql() as conn:
        rows.insert_outbox(
            conn, rows.MYSQL, pipeline_id=101, entity_type="ORDER", entity_key=entity_key,
            operation="Insert", row_version=1, message_id=mid, session_id=f"ORDER:{entity_key}",
            payload={"id": entity_key, "note": "task 26 P54 fixture"}, origin="1001", status="Failed",
            created_at_seconds_ago=20, sent_at_seconds_ago=None,
        )
    try:
        yield {"tag": tag, "message_id": mid}
    finally:
        with connections.mysql() as conn:
            rows.delete_by_tag(conn, rows.MYSQL, "outbox", tag)


def test_requires_auth(write_config, config_dict):
    client = _client(write_config, config_dict, offline=True)
    resp = client.get("/api/pipelines/101/errors")
    assert resp.status_code == 401


def test_404_on_unknown_pipeline_id(write_config, config_dict):
    client = _client(write_config, config_dict, offline=True)
    resp = client.get(f"/api/pipelines/999999/errors?{AUTH_QS}")
    assert resp.status_code == 404


def test_returns_the_real_seeded_failed_row(write_config, config_dict, failed_outbox_row):
    client = _client(write_config, config_dict)
    resp = client.get(f"/api/pipelines/101/errors?{AUTH_QS}")
    assert resp.status_code == 200
    body = resp.json()
    assert all(row["pipeline_id"] == 101 for row in body)
    matches = [row for row in body if row["message_id"] == failed_outbox_row["message_id"]]
    assert len(matches) == 1
    assert matches[0]["id"].startswith("outbox:")


def test_never_returns_another_pipelines_rows(write_config, config_dict, failed_outbox_row):
    client = _client(write_config, config_dict)
    resp = client.get(f"/api/pipelines/102/errors?{AUTH_QS}")
    assert resp.status_code == 200
    assert all(row["message_id"] != failed_outbox_row["message_id"] for row in resp.json())


class _FakeDriver:
    def __init__(self, server_id, pipeline_ids):
        self.server_id, self.pipeline_ids = server_id, pipeline_ids

    async def fetch_registry_rows(self):
        from types import SimpleNamespace

        return [SimpleNamespace(pipeline_id=p) for p in self.pipeline_ids]

    async def fetch_error_rows(self, limit):
        return [{"pipeline_id": p, "message_id": f"srv{self.server_id}-p{p}"} for p in self.pipeline_ids]


def _two_server_client(write_config, config_dict, monkeypatch, owned):
    from _http import pipeline_errors

    nodes = []
    for sid in (4001, 4002):
        node = _mysql_node(offline=True)
        node.update(node_id=f"n{sid}", server_id=sid)
        nodes.append(node)
    config_dict["machines"][0]["nodes"] = nodes
    monkeypatch.setattr(pipeline_errors, "build_driver", lambda server, settings: _FakeDriver(server.id, owned[server.id]))
    return TestClient(create_app(write_config(config_dict)))


def test_composite_id_selects_the_owning_server(write_config, config_dict, monkeypatch):
    client = _two_server_client(write_config, config_dict, monkeypatch, {4001: [1, 2, 4], 4002: [1, 4]})
    for server in (4001, 4002):
        resp = client.get(f"/api/pipelines/{server}%3A4/errors?{AUTH_QS}")
        assert resp.status_code == 200
        assert [r["message_id"] for r in resp.json()] == [f"srv{server}-p4"]


def test_bare_id_that_exists_on_one_server_still_works(write_config, config_dict, monkeypatch):
    client = _two_server_client(write_config, config_dict, monkeypatch, {4001: [1, 2, 4], 4002: [1, 4]})
    resp = client.get(f"/api/pipelines/2/errors?{AUTH_QS}")
    assert resp.status_code == 200 and [r["message_id"] for r in resp.json()] == ["srv4001-p2"]


def test_bare_id_on_several_servers_is_a_409_listing_candidates(write_config, config_dict, monkeypatch):
    client = _two_server_client(write_config, config_dict, monkeypatch, {4001: [1, 2, 4], 4002: [1, 4]})
    resp = client.get(f"/api/pipelines/4/errors?{AUTH_QS}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "ambiguous_pipeline"
    assert resp.json()["detail"]["candidates"] == ["4001:4", "4002:4"]


def test_composite_id_for_the_wrong_server_is_404_and_junk_is_400(write_config, config_dict, monkeypatch):
    client = _two_server_client(write_config, config_dict, monkeypatch, {4001: [1, 2, 4], 4002: [1, 4]})
    assert client.get(f"/api/pipelines/4002%3A2/errors?{AUTH_QS}").status_code == 404
    assert client.get(f"/api/pipelines/abc/errors?{AUTH_QS}").status_code == 400
