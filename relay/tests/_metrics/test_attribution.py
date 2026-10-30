from __future__ import annotations

from _contract.schema import RelayConfig
from _metrics.attribution import (
    HeartbeatAgeSmoother,
    ObjectSpec,
    RoutineNamesCache,
    affects_of,
    build_object_inventory,
    classify_heartbeat_staleness,
    compute_attribution,
    obj_status_for_reads,
    prune_stale_metadata,
    resolve_server_host_machine,
    root_cause_of,
)
from _metrics.down_confirm import DownConfirm
from _metrics.readability import EMITTED_STATUSES, ReadStatus


def _settings_dict(**overrides):
    base = {
        "auth": {
            "web_client_id": "w", "web_client_secret": "w",
            "app_client_id": "a", "app_client_secret": "a",
        },
        "ack_pin": "1234",
    }
    base.update(overrides)
    return base


def _config_with_one_bus_and_one_server() -> RelayConfig:
    return RelayConfig.model_validate(
        {
            "settings": _settings_dict(),
            "alerts": {},
            "service_busses": [
                {"id": 1, "name": "bus-1", "health_url": "http://localhost:5300/health", "amqp_port": 5672, "topic": "sync"},
            ],
            "machines": [
                {
                    "machine_id": 1, "machine_ip": "127.0.0.1", "machine_port": 5300,
                    "machine_name": "m1", "machine_type": "LOCAL", "machine_os": "Windows",
                    "nodes": [
                        {
                            "node_id": "node-mysql-1001", "node_type": "Database", "node_platform": "mysql",
                            "server_id": 1001,
                            "node_config": {"host": "h", "port": 3306, "user": "u", "password": "p", "database": "d"},
                        },
                    ],
                }
            ],
        }
    )


def test_build_object_inventory_p19():
    config = _config_with_one_bus_and_one_server()
    inventory = build_object_inventory(config)
    ids = {o.object_id for o in inventory}
    assert ids == {"bus:1:health", "bus:1:endpoint", "db:mysql:1001:query"}


def test_bus_endpoint_depends_on_bus_health_p20():
    config = _config_with_one_bus_and_one_server()
    inventory = {o.object_id: o for o in build_object_inventory(config)}
    assert inventory["bus:1:endpoint"].depends_on == "bus:1:health"
    assert inventory["bus:1:health"].depends_on is None


def test_db_query_object_has_no_upstream_dependency_yet_task_36_seam():
    config = _config_with_one_bus_and_one_server()
    inventory = {o.object_id: o for o in build_object_inventory(config)}
    assert inventory["db:mysql:1001:query"].depends_on is None


def test_obj_status_for_reads_returns_ok_when_never_observed():
    dc = DownConfirm()
    assert obj_status_for_reads(dc, "db:mysql:1001:query") == ReadStatus.OK


def test_obj_status_for_reads_returns_down_once_confirmed():
    dc = DownConfirm()
    dc.observe("x", ReadStatus.DOWN)
    assert obj_status_for_reads(dc, "x") == ReadStatus.DOWN


def test_obj_status_for_reads_returns_unread_during_an_active_unread_streak_p83():
    dc = DownConfirm()
    dc.observe("x", ReadStatus.OK)
    dc.observe("x", ReadStatus.OK)
    dc.observe("x", ReadStatus.UNREAD)
    assert obj_status_for_reads(dc, "x") == ReadStatus.UNREAD
    assert dc.confirmed_status("x") == ReadStatus.OK


def test_healthy_bus_emits_ok_for_both_objects():
    inventory = [
        ObjectSpec("bus:1:health", "bus_health"),
        ObjectSpec("bus:1:endpoint", "bus_endpoint", depends_on="bus:1:health"),
    ]
    dc = DownConfirm()
    dc.observe("bus:1:health", ReadStatus.OK)
    dc.observe("bus:1:endpoint", ReadStatus.OK)
    result = compute_attribution(inventory, dc)
    assert result.emitted["bus:1:health"] == ReadStatus.OK
    assert result.emitted["bus:1:endpoint"] == ReadStatus.OK
    assert "bus:1:endpoint" not in result.blocked_by


def test_endpoint_is_blocked_not_independently_down_when_health_is_down_p20_p24():
    inventory = [
        ObjectSpec("bus:1:health", "bus_health"),
        ObjectSpec("bus:1:endpoint", "bus_endpoint", depends_on="bus:1:health"),
    ]
    dc = DownConfirm()
    dc.observe("bus:1:health", ReadStatus.DOWN)
    dc.observe("bus:1:endpoint", ReadStatus.DOWN)

    result = compute_attribution(inventory, dc)

    assert result.emitted["bus:1:health"] == ReadStatus.DOWN
    assert result.emitted["bus:1:endpoint"] == ReadStatus.BLOCKED
    assert result.blocked_by["bus:1:endpoint"] == "bus:1:health"


def test_endpoint_is_blocked_even_if_its_own_raw_observation_never_ran_p24():
    inventory = [
        ObjectSpec("bus:1:health", "bus_health"),
        ObjectSpec("bus:1:endpoint", "bus_endpoint", depends_on="bus:1:health"),
    ]
    dc = DownConfirm()
    dc.observe("bus:1:health", ReadStatus.DOWN)

    result = compute_attribution(inventory, dc)
    assert result.emitted["bus:1:endpoint"] == ReadStatus.BLOCKED


def test_emitted_is_always_a_member_of_emitted_statuses_p22():
    inventory = [
        ObjectSpec("bus:1:health", "bus_health"),
        ObjectSpec("bus:1:endpoint", "bus_endpoint", depends_on="bus:1:health"),
        ObjectSpec("db:mysql:1001:query", "db_query"),
    ]
    dc = DownConfirm()
    dc.observe("bus:1:health", ReadStatus.OK)
    dc.observe("bus:1:endpoint", ReadStatus.OK)
    dc.observe("db:mysql:1001:query", ReadStatus.OK)
    dc.observe("db:mysql:1001:query", ReadStatus.UNREAD)

    result = compute_attribution(inventory, dc)
    assert set(result.emitted.values()) <= EMITTED_STATUSES
    assert result.read_status["db:mysql:1001:query"] == ReadStatus.UNREAD
    assert result.emitted["db:mysql:1001:query"] == ReadStatus.OK


def test_attribution_dependency_cycle_raises_rather_than_hanging():
    inventory = [
        ObjectSpec("a", "db_query", depends_on="b"),
        ObjectSpec("b", "db_query", depends_on="a"),
    ]
    dc = DownConfirm()
    try:
        compute_attribution(inventory, dc)
        assert False, "expected ValueError"
    except ValueError:
        pass


def _three_level_inventory() -> list[ObjectSpec]:
    return [
        ObjectSpec("bus:1:health", "bus_health"),
        ObjectSpec("bus:1:endpoint", "bus_endpoint", depends_on="bus:1:health"),
        ObjectSpec("downstream:x", "db_query", depends_on="bus:1:endpoint"),
    ]


def test_root_cause_of_a_healthy_object_is_none():
    inventory = [ObjectSpec("bus:1:health", "bus_health")]
    dc = DownConfirm()
    dc.observe("bus:1:health", ReadStatus.OK)
    result = compute_attribution(inventory, dc)
    assert root_cause_of("bus:1:health", result) is None


def test_root_cause_of_a_directly_down_object_is_itself():
    inventory = [ObjectSpec("bus:1:health", "bus_health")]
    dc = DownConfirm()
    dc.observe("bus:1:health", ReadStatus.DOWN)
    result = compute_attribution(inventory, dc)
    assert root_cause_of("bus:1:health", result) == "bus:1:health"


def test_root_cause_of_a_blocked_object_is_its_upstream_p86():
    inventory = _three_level_inventory()
    dc = DownConfirm()
    dc.observe("bus:1:health", ReadStatus.DOWN)
    result = compute_attribution(inventory, dc)
    assert root_cause_of("downstream:x", result) == "bus:1:health"
    assert root_cause_of("bus:1:endpoint", result) == "bus:1:health"


def test_root_cause_of_treats_unread_as_down_p86_without_changing_the_emitted_wire_value():
    inventory = [ObjectSpec("bus:1:health", "bus_health")]
    dc = DownConfirm()
    dc.observe("bus:1:health", ReadStatus.OK)
    dc.observe("bus:1:health", ReadStatus.UNREAD)

    result = compute_attribution(inventory, dc)
    assert result.emitted["bus:1:health"] == ReadStatus.OK
    assert root_cause_of("bus:1:health", result) == "bus:1:health"


def test_affects_of_returns_every_transitively_blocked_object_p87():
    inventory = _three_level_inventory()
    dc = DownConfirm()
    dc.observe("bus:1:health", ReadStatus.DOWN)
    result = compute_attribution(inventory, dc)
    assert set(affects_of("bus:1:health", result)) == {"bus:1:endpoint", "downstream:x"}
    assert affects_of("bus:1:endpoint", result) == ["downstream:x"]
    assert affects_of("downstream:x", result) == []


def test_heartbeat_staleness_ok_below_the_2x_bar():
    assert classify_heartbeat_staleness(15, ping_seconds=10) == "ok"


def test_heartbeat_staleness_blocked_by_relay_at_the_2x_bar():
    assert classify_heartbeat_staleness(20, ping_seconds=10) == "blocked_by_relay"
    assert classify_heartbeat_staleness(25, ping_seconds=10) == "blocked_by_relay"


def test_heartbeat_staleness_down_at_the_3x_bar():
    assert classify_heartbeat_staleness(30, ping_seconds=10) == "down"
    assert classify_heartbeat_staleness(100, ping_seconds=10) == "down"


def test_heartbeat_staleness_never_seen_is_ok_not_down():
    assert classify_heartbeat_staleness(None, ping_seconds=10) == "ok"


def test_heartbeat_age_smoother_holds_age_at_zero_for_exactly_one_poll_after_recovery():
    smoother = HeartbeatAgeSmoother()
    assert smoother.smoothed_age("p1", raw_age_seconds=45.0, currently_down_or_blocked=True) == 45.0
    assert smoother.smoothed_age("p1", raw_age_seconds=12.0, currently_down_or_blocked=False) == 0.0
    assert smoother.smoothed_age("p1", raw_age_seconds=3.0, currently_down_or_blocked=False) == 3.0


def test_heartbeat_age_smoother_does_not_smooth_when_never_previously_bad():
    smoother = HeartbeatAgeSmoother()
    assert smoother.smoothed_age("p2", raw_age_seconds=5.0, currently_down_or_blocked=False) == 5.0


def test_heartbeat_age_smoother_tracks_objects_independently():
    smoother = HeartbeatAgeSmoother()
    smoother.smoothed_age("a", raw_age_seconds=99.0, currently_down_or_blocked=True)
    smoother.smoothed_age("b", raw_age_seconds=1.0, currently_down_or_blocked=False)
    assert smoother.smoothed_age("a", raw_age_seconds=2.0, currently_down_or_blocked=False) == 0.0
    assert smoother.smoothed_age("b", raw_age_seconds=2.0, currently_down_or_blocked=False) == 2.0


def test_prune_stale_metadata_drops_unknown_keys():
    meta = {"p1": {"x": 1}, "p2": {"x": 2}, "removed": {"x": 3}}
    pruned = prune_stale_metadata(meta, known_object_ids=["p1", "p2"])
    assert pruned == {"p1": {"x": 1}, "p2": {"x": 2}}


def test_prune_stale_metadata_does_not_mutate_the_input():
    meta = {"p1": {"x": 1}, "removed": {"x": 3}}
    prune_stale_metadata(meta, known_object_ids=["p1"])
    assert "removed" in meta


async def test_routine_names_cache_reuses_within_ttl():
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        return {"routine_a", "routine_b"}

    now = {"t": 0.0}
    cache = RoutineNamesCache(triggers_cache_ms=60000, now_ms=lambda: now["t"])

    first = await cache.get_or_fetch("server-1", fetch)
    now["t"] = 30000.0
    second = await cache.get_or_fetch("server-1", fetch)

    assert first == second == {"routine_a", "routine_b"}
    assert calls["n"] == 1


async def test_routine_names_cache_refetches_after_ttl_expires():
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        return {"routine_a"}

    now = {"t": 0.0}
    cache = RoutineNamesCache(triggers_cache_ms=60000, now_ms=lambda: now["t"])

    await cache.get_or_fetch("server-1", fetch)
    now["t"] = 60001.0
    await cache.get_or_fetch("server-1", fetch)

    assert calls["n"] == 2


async def test_routine_names_cache_is_independent_per_server():
    async def fetch_a():
        return {"a"}

    async def fetch_b():
        return {"b"}

    cache = RoutineNamesCache()
    result_a = await cache.get_or_fetch("server-a", fetch_a)
    result_b = await cache.get_or_fetch("server-b", fetch_b)
    assert result_a == {"a"}
    assert result_b == {"b"}


def _config_with_one_server_hosted_on_one_machine() -> RelayConfig:
    return RelayConfig.model_validate(
        {
            "settings": _settings_dict(),
            "alerts": {},
            "machines": [
                {
                    "machine_id": 5,
                    "machine_ip": "127.0.0.1",
                    "machine_port": 29105,
                    "machine_name": "m5",
                    "machine_type": "LOCAL",
                    "machine_os": "Windows",
                    "nodes": [
                        {
                            "node_id": "n1",
                            "node_type": "Database",
                            "node_platform": "mysql",
                            "node_config": {"host": "h", "port": 3306, "user": "u", "password": "p", "database": "d"},
                            "server_id": 1001,
                        }
                    ],
                }
            ],
        }
    )


def test_resolve_server_host_machine_maps_server_id_to_hosting_machine_id():
    config = _config_with_one_server_hosted_on_one_machine()
    assert resolve_server_host_machine(config) == {1001: 5}


def test_resolve_server_host_machine_is_empty_when_no_machines_are_configured():
    config = RelayConfig.model_validate(
        {
            "settings": _settings_dict(),
            "alerts": {},
            "machines": [],
        }
    )
    assert config.servers == []
    assert resolve_server_host_machine(config) == {}


def test_build_object_inventory_unaffected_when_no_machine_is_offline_task_36():
    config = _config_with_one_server_hosted_on_one_machine()
    inventory = build_object_inventory(config)
    by_id = {o.object_id: o for o in inventory}
    assert "machine:5" not in by_id
    assert by_id["db:mysql:1001:query"].depends_on is None


def test_build_object_inventory_attaches_machine_dependency_when_offline_task_36():
    config = _config_with_one_server_hosted_on_one_machine()
    inventory = build_object_inventory(config, offline_machine_ids=frozenset({5}))
    by_id = {o.object_id: o for o in inventory}
    assert by_id["db:mysql:1001:query"].depends_on == "machine:5"
    assert by_id["machine:5"].depends_on is None
    assert by_id["machine:5"].kind == "machine"


def test_compute_attribution_needs_zero_changes_for_the_machine_layer_task_36():
    config = _config_with_one_server_hosted_on_one_machine()
    inventory = build_object_inventory(config, offline_machine_ids=frozenset({5}))

    down_confirm = DownConfirm()
    down_confirm.observe("machine:5", ReadStatus.DOWN)
    down_confirm.observe("db:mysql:1001:query", ReadStatus.OK)

    result = compute_attribution(inventory, down_confirm)

    assert result.emitted["machine:5"] == ReadStatus.DOWN
    assert result.emitted["db:mysql:1001:query"] == ReadStatus.BLOCKED
    assert result.blocked_by["db:mysql:1001:query"] == "machine:5"
    assert set(result.emitted.values()) <= EMITTED_STATUSES

    assert root_cause_of("db:mysql:1001:query", result) == "machine:5"
    assert affects_of("machine:5", result) == ["db:mysql:1001:query"]


def test_compute_attribution_no_blocked_status_when_machine_online_task_36():
    config = _config_with_one_server_hosted_on_one_machine()
    inventory = build_object_inventory(config, offline_machine_ids=frozenset())

    down_confirm = DownConfirm()
    down_confirm.observe("db:mysql:1001:query", ReadStatus.OK)

    result = compute_attribution(inventory, down_confirm)

    assert result.emitted["db:mysql:1001:query"] == ReadStatus.OK
    assert "db:mysql:1001:query" not in result.blocked_by
    assert root_cause_of("db:mysql:1001:query", result) is None
