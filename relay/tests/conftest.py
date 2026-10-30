from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pytest

from _fixtures import env

from ._db_lock import acquire as _acquire_db_lock
from ._db_lock import release as _release_db_lock


def pytest_collection_modifyitems(config, items):
    if env.live_lab_enabled():
        return
    skip = pytest.mark.skip(reason="needs the real lab; set RELAY_LIVE_LAB=1")
    for item in items:
        if "live_lab" in item.keywords:
            item.add_marker(skip)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if call.excinfo is not None and call.excinfo.errisinstance(env.LiveLabDisabledError) and call.when in ("setup", "call"):
        rep.outcome = "skipped"
        path, lineno, _ = item.reportinfo()
        rep.longrepr = (str(path), lineno + 1, "Skipped: needs the real lab; set RELAY_LIVE_LAB=1")


TEST_TOKEN = "test-session-token-0123456789abcdef0123456789abcdef"
TEST_ACCOUNT_SECRET = "test-secret"


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path_factory, monkeypatch):
    import time

    from _push.accounts import account_key
    from _session.store import hash_token

    state = tmp_path_factory.mktemp("relay_state")
    monkeypatch.setenv("RELAY_STATE_DIR", str(state))
    accounts = state / "auth" / "push_accounts.json"
    accounts.parent.mkdir(parents=True)
    accounts.write_text(json.dumps([
        {"account_secret": TEST_ACCOUNT_SECRET, "name": "Test", "min_severity": 5, "device_codes": []},
    ]))
    now = time.time()
    sessions = state / "auth" / "sessions.json"
    sessions.write_text(json.dumps({"sessions": [{
        "hash": hash_token(TEST_TOKEN), "account": "Test", "account_key": account_key(TEST_ACCOUNT_SECRET),
        "device_name": "pytest", "platform": "web", "created_at": now, "last_used_at": now, "expires_at": now + 10 * 365 * 86400,
        "push_token": None,
    }]}))


@pytest.fixture(scope="session", autouse=True)
def _serialize_pytest_runs():
    _acquire_db_lock()
    try:
        yield
    finally:
        _release_db_lock()


def make_config_dict(**overrides: Any) -> dict:
    base: dict[str, Any] = {
        "settings": {
            "bind_host": "127.0.0.1",
            "port": 8085,
            "auth": {
                "web_client_id": "test_web_id",
                "web_client_secret": "test_web_secret",
                "app_client_id": "test_app_id",
                "app_client_secret": "test_app_secret",
            },
            "ack_pin": "1234",
        },
        "alerts": {},
        "machines": [
            {
                "machine_id": 1,
                "machine_ip": "127.0.0.1",
                "machine_port": 29101,
                "machine_name": "test-machine-1",
                "machine_type": "LOCAL",
                "machine_os": "Windows",
                "nodes": [],
            }
        ],
    }
    base.update(overrides)
    return base


def make_database_node(
    node_id: str,
    server_id: int,
    *,
    platform: str = "mysql",
    host: str = "127.0.0.1",
    port: int = 3306,
    user: str = "u",
    password: str = "p",
    database: str = "db",
    used_buses: Optional[list] = None,
    node_name: Optional[str] = None,
) -> dict:
    return {
        "node_id": node_id,
        "node_name": node_name or node_id,
        "node_type": "Database",
        "node_platform": platform,
        "server_id": server_id,
        "node_config": {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "used_buses": used_buses or [],
        },
    }


def make_bus_node(
    node_id: str,
    server_id: int,
    *,
    health_url: str = "http://127.0.0.1:5300/health",
    amqp_port: int = 5672,
    topic: str = "sync",
    node_name: Optional[str] = None,
) -> dict:
    return {
        "node_id": node_id,
        "node_name": node_name or node_id,
        "node_type": "ServiceBus",
        "node_platform": "service_bus",
        "server_id": server_id,
        "node_config": {"health_url": health_url, "amqp_port": amqp_port, "topic": topic},
    }


@pytest.fixture
def config_dict() -> dict:
    return make_config_dict()


@pytest.fixture
def write_config(tmp_path: Path):
    path = tmp_path / "config.json"

    def _write(data: dict) -> Path:
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    return _write


def _scenario_factory():
    def _fixture():
        from _fixtures import scenarios as scenarios_module
        from _fixtures import elevation

        from _fixtures.harness import seed_with_recovery, teardown_all

        seeded: list[tuple] = []

        def _use(code: str):
            scn = scenarios_module.get(code)
            if scn.elevated and not elevation.is_elevated():
                pytest.skip(
                    f"{code} requires an elevated (Run as Administrator) pytest "
                    "process to stop/start a real Windows service — see "
                    "relay/README.md 'Elevated fixture subset' for how to run it."
                )
            return seed_with_recovery(scn, code, seeded)

        yield _use

        teardown_all(seeded)

    return pytest.fixture(_fixture)


mysql_scenario = _scenario_factory()
postgresql_scenario = _scenario_factory()
mssql_scenario = _scenario_factory()
service_bus_scenario = _scenario_factory()
