import asyncio
import json
import time
from pathlib import Path

import pytest

from _lib.config import ConfigRefreshLoop, ConfigStore, StartupConfigError


def test_load_startup_accepts_valid_config(write_config, config_dict):
    path = write_config(config_dict)
    store = ConfigStore(path)
    config = store.load_startup()
    assert config.settings.auth.web_client_id == "test_web_id"


def test_load_startup_refuses_missing_file(tmp_path: Path):
    store = ConfigStore(tmp_path / "does_not_exist.json")
    with pytest.raises(StartupConfigError):
        store.load_startup()


def test_load_startup_refuses_invalid_json(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = ConfigStore(path)
    with pytest.raises(StartupConfigError):
        store.load_startup()


def test_load_startup_refuses_missing_required_credential(write_config, config_dict):
    del config_dict["settings"]["ack_pin"]
    path = write_config(config_dict)
    store = ConfigStore(path)
    with pytest.raises(StartupConfigError):
        store.load_startup()


def test_load_startup_refuses_duplicate_machine_ids(write_config, config_dict):
    config_dict["machines"].append(dict(config_dict["machines"][0]))
    path = write_config(config_dict)
    store = ConfigStore(path)
    with pytest.raises(StartupConfigError):
        store.load_startup()


def test_load_startup_refuses_server_used_buses_referencing_unknown_bus(write_config, config_dict):
    config_dict["machines"][0]["nodes"] = [
        {
            "node_id": "n1",
            "node_type": "Database",
            "node_platform": "mysql",
            "server_id": 1001,
            "node_config": {
                "host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "db",
                "used_buses": [999],
            },
        }
    ]
    path = write_config(config_dict)
    store = ConfigStore(path)
    with pytest.raises(StartupConfigError):
        store.load_startup()


def test_load_startup_accepts_server_used_buses_referencing_a_real_bus(write_config, config_dict):
    config_dict["service_busses"] = [
        {"id": 1, "name": "bus-1", "health_url": "http://127.0.0.1:5300/health", "amqp_port": 5671, "topic": "t"},
    ]
    config_dict["machines"][0]["nodes"] = [
        {
            "node_id": "n1",
            "node_type": "Database",
            "node_platform": "mysql",
            "server_id": 1001,
            "node_config": {
                "host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "db",
                "used_buses": [1],
            },
        },
    ]
    path = write_config(config_dict)
    store = ConfigStore(path)
    config = store.load_startup()
    assert config.servers[0].used_buses == [1]


def test_load_startup_refuses_service_bus_as_a_machine_node(write_config, config_dict):
    config_dict["machines"][0]["nodes"] = [
        {
            "node_id": "n2",
            "node_type": "ServiceBus",
            "node_platform": "service_bus",
            "server_id": 1,
            "node_config": {"health_url": "http://127.0.0.1:5300/health", "amqp_port": 5671, "topic": "t"},
        },
    ]
    path = write_config(config_dict)
    store = ConfigStore(path)
    with pytest.raises(StartupConfigError):
        store.load_startup()


def test_load_startup_refuses_duplicate_node_id_within_one_machine(write_config, config_dict):
    node = {
        "node_id": "dup",
        "node_type": "Database",
        "node_platform": "mysql",
        "server_id": 1001,
        "node_config": {"host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "db"},
    }
    other = dict(node, server_id=1002, node_config=dict(node["node_config"], database="db2"))
    config_dict["machines"][0]["nodes"] = [node, other]
    path = write_config(config_dict)
    store = ConfigStore(path)
    with pytest.raises(StartupConfigError):
        store.load_startup()


def test_load_startup_refuses_duplicate_bus_id(write_config, config_dict):
    config_dict["service_busses"] = [
        {"id": 1, "name": "bus-1", "health_url": "http://127.0.0.1:5300/health", "amqp_port": 5671, "topic": "t"},
        {"id": 1, "name": "bus-1-dup", "health_url": "http://127.0.0.1:5301/health", "amqp_port": 5672, "topic": "t2"},
    ]
    path = write_config(config_dict)
    store = ConfigStore(path)
    with pytest.raises(StartupConfigError):
        store.load_startup()


def test_load_startup_refuses_duplicate_server_id_among_database_nodes(write_config, config_dict):
    config_dict["machines"][0]["nodes"] = [
        {
            "node_id": "n1", "node_type": "Database", "node_platform": "mysql", "server_id": 1001,
            "node_config": {"host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "db"},
        },
        {
            "node_id": "n2", "node_type": "Database", "node_platform": "postgresql", "server_id": 1001,
            "node_config": {"host": "127.0.0.1", "port": 5432, "user": "u", "password": "p", "database": "db2"},
        },
    ]
    path = write_config(config_dict)
    store = ConfigStore(path)
    with pytest.raises(StartupConfigError):
        store.load_startup()


def test_load_startup_accepts_database_node_with_a_real_server_id(write_config, config_dict):
    config_dict["machines"][0]["nodes"] = [
        {
            "node_id": "n1",
            "node_type": "Database",
            "node_platform": "mysql",
            "server_id": 1001,
            "node_config": {"host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "db"},
        }
    ]
    path = write_config(config_dict)
    store = ConfigStore(path)
    config = store.load_startup()
    assert config.machines[0].nodes[0].server_id == 1001
    assert config.servers[0].id == 1001


def test_get_before_load_startup_raises_runtime_error(tmp_path: Path):
    store = ConfigStore(tmp_path / "config.json")
    with pytest.raises(RuntimeError):
        store.get()


def test_get_returns_same_object_when_file_unchanged(write_config, config_dict):
    path = write_config(config_dict)
    store = ConfigStore(path)
    store.load_startup()
    first = store.get()
    second = store.get()
    assert first is second


def test_get_picks_up_a_real_change(write_config, config_dict):
    path = write_config(config_dict)
    store = ConfigStore(path)
    store.load_startup()
    first = store.get()

    config_dict["settings"]["ack_pin"] = "9999"
    time.sleep(0.02)
    write_config(config_dict)

    second = store.get()
    assert second is not first
    assert second.settings.ack_pin == "9999"


def test_get_falls_back_to_last_known_good_on_bad_reload(write_config, config_dict):
    path = write_config(config_dict)
    store = ConfigStore(path)
    store.load_startup()
    good = store.get()

    time.sleep(0.02)
    path.write_text("{this is not valid json", encoding="utf-8")

    result = store.get()
    assert result is good
    assert result.settings.auth.web_client_id == "test_web_id"


def test_get_falls_back_to_last_known_good_on_valid_json_invalid_schema(write_config, config_dict):
    path = write_config(config_dict)
    store = ConfigStore(path)
    store.load_startup()
    good = store.get()

    time.sleep(0.02)
    broken = dict(config_dict)
    del broken["settings"]["ack_pin"]
    path.write_text(json.dumps(broken), encoding="utf-8")

    result = store.get()
    assert result is good
    assert result.settings.auth.web_client_id == "test_web_id"


def test_get_recovers_once_the_file_is_fixed_again(write_config, config_dict):
    path = write_config(config_dict)
    store = ConfigStore(path)
    store.load_startup()

    time.sleep(0.02)
    path.write_text("{broken", encoding="utf-8")
    broken_read = store.get()

    config_dict["settings"]["ack_pin"] = "5678"
    time.sleep(0.02)
    write_config(config_dict)
    fixed = store.get()

    assert fixed is not broken_read
    assert fixed.settings.ack_pin == "5678"


def test_force_reload_before_load_startup_raises_runtime_error(tmp_path: Path):
    store = ConfigStore(tmp_path / "config.json")
    with pytest.raises(RuntimeError):
        store.force_reload()


def test_force_reload_rereads_even_with_an_unchanged_mtime(write_config, config_dict):
    path = write_config(config_dict)
    store = ConfigStore(path)
    store.load_startup()
    original_mtime = path.stat().st_mtime

    config_dict["settings"]["ack_pin"] = "5678"
    path.write_text(json.dumps(config_dict), encoding="utf-8")
    os_stat_mtime_matches = path.stat().st_mtime == original_mtime

    ok, error = store.force_reload()
    assert ok is True
    assert error is None
    assert store.get().settings.ack_pin == "5678"
    del os_stat_mtime_matches


def test_force_reload_reports_failure_and_keeps_last_known_good(write_config, config_dict):
    path = write_config(config_dict)
    store = ConfigStore(path)
    store.load_startup()
    good = store.get()

    path.write_text("{not valid json", encoding="utf-8")

    ok, error = store.force_reload()
    assert ok is False
    assert error
    assert store.get() is good


def test_get_never_drops_already_known_machines_on_bad_reload(write_config, config_dict):
    path = write_config(config_dict)
    store = ConfigStore(path)
    store.load_startup()
    good = store.get()
    assert len(good.machines) == 1

    time.sleep(0.02)
    path.write_text("not json at all", encoding="utf-8")

    result = store.get()
    assert len(result.machines) == 1
    assert result.machines[0].machine_id == 1


async def test_config_refresh_loop_force_reloads_on_its_own_interval(write_config, config_dict):
    config_dict["settings"]["config_force_reload_seconds"] = 0
    path = write_config(config_dict)
    store = ConfigStore(path)
    store.load_startup()

    config_dict["settings"]["ack_pin"] = "9999"
    path.write_text(json.dumps(config_dict), encoding="utf-8")

    loop = ConfigRefreshLoop(store)
    task = asyncio.create_task(loop.run_forever())
    try:
        for _ in range(200):
            if store.get().settings.ack_pin == "9999":
                break
            await asyncio.sleep(0.01)
        assert store.get().settings.ack_pin == "9999"
    finally:
        loop.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_config_refresh_loop_never_raises_on_a_broken_config(write_config, config_dict):
    config_dict["settings"]["config_force_reload_seconds"] = 0
    path = write_config(config_dict)
    store = ConfigStore(path)
    store.load_startup()
    good = store.get()

    path.write_text("{not valid json at all", encoding="utf-8")

    loop = ConfigRefreshLoop(store)
    task = asyncio.create_task(loop.run_forever())
    try:
        await asyncio.sleep(0.1)
        assert store.get() is good
        assert not task.done()
    finally:
        loop.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_config_refresh_loop_stop_ends_run_forever(write_config, config_dict):
    config_dict["settings"]["config_force_reload_seconds"] = 0
    path = write_config(config_dict)
    store = ConfigStore(path)
    store.load_startup()

    loop = ConfigRefreshLoop(store)
    task = asyncio.create_task(loop.run_forever())
    await asyncio.sleep(0.05)
    loop.stop()
    await asyncio.wait_for(task, timeout=3.0)
    assert task.done()
