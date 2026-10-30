import pytest
from pydantic import ValidationError

from _contract.schema import RelayConfig
from config_issues import compute_config_issues


def _database_node(node_id, server_id, used_buses=None):
    return {
        "node_id": node_id,
        "node_name": f"srv-{server_id}",
        "node_type": "Database",
        "node_platform": "mysql",
        "server_id": server_id,
        "node_config": {
            "host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "db",
            "used_buses": used_buses or [],
        },
    }


def _bus(bus_id):
    return {
        "id": bus_id,
        "name": f"bus-{bus_id}",
        "health_url": "http://127.0.0.1:5300/health",
        "amqp_port": 5671,
        "topic": "t",
    }


def _codes(config_dict: dict) -> set[str]:
    config = RelayConfig.model_validate(config_dict)
    return {issue.code for issue in compute_config_issues(config)}


def test_no_issues_on_a_clean_minimal_config(config_dict):
    config_dict["service_busses"] = [_bus(1)]
    config_dict["machines"][0]["nodes"] = [
        _database_node("n1", 1001, used_buses=[1]),
    ]
    assert compute_config_issues(RelayConfig.model_validate(config_dict)) == []


def test_server_with_no_used_buses_is_flagged(config_dict):
    config_dict["machines"][0]["nodes"] = [_database_node("n1", 1001)]
    assert "server_unused_by_any_bus" in _codes(config_dict)


def test_bus_not_referenced_by_any_server_is_flagged(config_dict):
    config_dict["service_busses"] = [_bus(1)]
    assert "service_bus_unused_by_any_server" in _codes(config_dict)


def test_bus_referenced_by_a_server_is_not_flagged(config_dict):
    config_dict["service_busses"] = [_bus(1)]
    config_dict["machines"][0]["nodes"] = [
        _database_node("n2", 1001, used_buses=[1]),
    ]
    codes = _codes(config_dict)
    assert "service_bus_unused_by_any_server" not in codes
    assert "server_unused_by_any_bus" not in codes


def test_machine_without_nodes_is_valid_and_raises_nothing(config_dict):
    assert config_dict["machines"][0]["nodes"] == []
    assert "machine_without_nodes" not in _codes(config_dict)
    assert _codes(config_dict) == set()


def test_unknown_severity_channel_now_hard_refuses_at_the_schema_level_not_a_soft_issue(config_dict):
    config_dict["alerts"] = {"severity_channels": {"1": ["carrier_pigeon"]}}
    with pytest.raises(ValidationError):
        RelayConfig.model_validate(config_dict)


def test_known_severity_channel_is_not_flagged(config_dict):
    config_dict["alerts"] = {"severity_channels": {"1": ["email", "sms"]}}
    assert "severity_channel_references_unknown_channel" not in _codes(config_dict)


def test_object_severity_out_of_range_is_flagged(config_dict):
    config_dict["alerts"] = {"object_severities": {"bus": 9, "db": 2, "machine": 2}}
    assert "object_severity_out_of_range" in _codes(config_dict)


def test_issue_ordering_is_servers_then_buses_then_machines_then_alerts(config_dict):
    config_dict["service_busses"] = [_bus(1)]
    config_dict["machines"][0]["nodes"] = [_database_node("n1", 1001)]
    config_dict["alerts"] = {"object_severities": {"bus": 9, "db": 2, "machine": 2}}
    config = RelayConfig.model_validate(config_dict)
    codes = [issue.code for issue in compute_config_issues(config)]
    assert codes == [
        "server_unused_by_any_bus",
        "service_bus_unused_by_any_server",
        "object_severity_out_of_range",
    ]


def _orphan_config(config_dict):
    config_dict["machines"][0]["nodes"] = [_database_node("n1", 1001), _database_node("n2", 1002), _database_node("n3", 1003)]
    config_dict["machines"][0]["nodes"][1]["node_config"]["database"] = "db_1"
    config_dict["machines"][0]["nodes"][2]["node_config"]["database"] = "db_2"
    return RelayConfig.model_validate(config_dict)


def test_orphan_pipeline_rows_are_flagged_as_errors_naming_machine_service_and_server(config_dict):
    from config_issues import orphan_pipeline_ids, orphan_pipeline_issues

    config = _orphan_config(config_dict)
    rows = [
        {"pipelineId": 7, "sourceSystem": "1001", "targetSystem": "2001", "_hostServerId": 1001},
        {"pipelineId": 8, "sourceSystem": "2001", "targetSystem": "1001", "_hostServerId": 1001},
        {"pipelineId": 9, "sourceSystem": "1001", "targetSystem": "1001", "_hostServerId": 1001},
    ]
    assert orphan_pipeline_ids(config, rows) == {7, 8}
    outgoing, incoming = orphan_pipeline_issues(config, rows)
    machine = config.machines[0]
    prefix = f"{machine.machine_name} ({machine.machine_id}) → srv-1001 (1001): "
    assert outgoing.severity == incoming.severity == "error"
    assert outgoing.code == "pipeline_references_unknown_server"
    assert outgoing.message == (
        prefix + "outgoing pipeline 7 targets server 2001, which is not configured. "
        "Remove it from sync_pipeline or add server 2001 to the config."
    )
    assert incoming.message == (
        prefix + "incoming pipeline 8 comes from server 2001, which is not configured. "
        "Remove it from sync_pipeline or add server 2001 to the config."
    )
    assert outgoing.where["pipelineIds"] == [7] and outgoing.where["serverId"] == "1001"


def test_rows_are_checked_on_every_configured_host(config_dict):
    from config_issues import orphan_pipeline_issues

    config = _orphan_config(config_dict)
    rows = [
        {"pipelineId": 2, "sourceSystem": "1001", "targetSystem": "2001", "_hostServerId": 1001},
        {"pipelineId": 2, "sourceSystem": "1002", "targetSystem": "2001", "_hostServerId": 1002},
    ]
    hosts = {i.where["serverId"] for i in orphan_pipeline_issues(config, rows) if i.code == "pipeline_references_unknown_server"}
    assert hosts == {"1001", "1002"}


def test_rows_with_neither_side_equal_to_the_host_get_one_error_each(config_dict):
    from config_issues import is_foreign_row, orphan_pipeline_issues

    config = _orphan_config(config_dict)
    rows = [
        {"pipelineId": 1, "sourceSystem": "1001", "targetSystem": "1002", "_hostServerId": 1003},
        {"pipelineId": 3, "sourceSystem": "1001", "targetSystem": "1002", "_hostServerId": 1003},
        {"pipelineId": 5, "sourceSystem": "1002", "targetSystem": "1001", "_hostServerId": 1003},
        {"pipelineId": 6, "sourceSystem": "1001", "targetSystem": "1002", "_hostServerId": 1001},
    ]
    issues = orphan_pipeline_issues(config, rows)
    assert [i.where["pipelineIds"] for i in issues] == [[1], [3], [5]]
    assert all(i.code == "pipeline_names_other_server" and i.severity == "error" for i in issues)
    assert issues[0].message == (
        "test-machine-1 (1) → srv-1003 (1003): pipeline 1 has source 1001 and target 1002 — "
        "neither is this server (1003). Fix source_system/target_system in sync_pipeline."
    )
    assert [is_foreign_row(r) for r in rows] == [True, True, True, False]


def test_a_foreign_row_that_also_names_an_unknown_server_gets_only_the_self_mismatch_issue(config_dict):
    from config_issues import orphan_pipeline_issues

    config = _orphan_config(config_dict)
    rows = [{"pipelineId": 9, "sourceSystem": "1001", "targetSystem": "2001", "_hostServerId": 1003}]
    [issue] = orphan_pipeline_issues(config, rows)
    assert issue.code == "pipeline_names_other_server"


def test_owners_cloned_database_case_four_rows_in_4002_naming_4001(config_dict):
    from config_issues import orphan_pipeline_issues

    nodes = []
    for node_id, server_id, database in (("n1", 4001, "db"), ("n2", 4002, "db_1"), ("n3", 1001, "x1"), ("n4", 3001, "x3"), ("n5", 5001, "x5")):
        node = _database_node(node_id, server_id)
        node["node_name"] = "MySQL 1" if server_id == 4002 else "MySQL"
        node["node_config"]["database"] = database
        nodes.append(node)
    config_dict["machines"][0].update(machine_id=4, machine_name="Tabish-pc", nodes=nodes)
    config = RelayConfig.model_validate(config_dict)
    rows = [
        {"pipelineId": pid, "sourceSystem": "4001", "targetSystem": str(target), "_hostServerId": host}
        for host in (4001, 4002)
        for pid, target in ((1, 1001), (2, 2001), (3, 3001), (4, 5001))
    ]
    issues = orphan_pipeline_issues(config, rows)
    foreign = [i for i in issues if i.code == "pipeline_names_other_server"]
    assert [i.where["pipelineIds"][0] for i in foreign] == [1, 2, 3, 4]
    assert foreign[2].message == (
        "Tabish-pc (4) → MySQL 1 (4002): pipeline 3 has source 4001 and target 3001 — "
        "neither is this server (4002). Fix source_system/target_system in sync_pipeline."
    )
    [orphan] = [i for i in issues if i.code == "pipeline_references_unknown_server"]
    assert orphan.where["serverId"] == "4001" and orphan.where["pipelineIds"] == [2]
    assert orphan.message == (
        "Tabish-pc (4) → MySQL (4001): outgoing pipeline 2 targets server 2001, which is not configured. "
        "Remove it from sync_pipeline or add server 2001 to the config."
    )
    assert len(issues) == 5


def test_clean_rows_raise_nothing(config_dict):
    from config_issues import orphan_pipeline_issues

    config = _orphan_config(config_dict)
    rows = [{"pipelineId": 1, "sourceSystem": "1001", "targetSystem": "1002", "_hostServerId": 1001}]
    assert orphan_pipeline_issues(config, rows) == []


def test_two_nodes_on_one_database_are_flagged(config_dict):
    config_dict["service_busses"] = [_bus(1)]
    config_dict["machines"][0]["nodes"] = [_database_node("n1", 4001, [1]), _database_node("n2", 4002, [1])]
    assert "servers_share_one_database" in _codes(config_dict)


def test_two_nodes_on_different_databases_of_one_host_are_fine(config_dict):
    config_dict["service_busses"] = [_bus(1)]
    other = _database_node("n2", 4002, [1])
    other["node_config"]["database"] = "db_1"
    config_dict["machines"][0]["nodes"] = [_database_node("n1", 4001, [1]), other]
    assert "servers_share_one_database" not in _codes(config_dict)


def test_disabled_pipelines_raise_no_orphan_or_foreign_issue_and_enabled_ones_do(config_dict):
    from config_issues import orphan_pipeline_ids, orphan_pipeline_issues

    config = _orphan_config(config_dict)
    outgoing = {"pipelineId": 2, "sourceSystem": "1001", "targetSystem": "2001", "_hostServerId": 1001}
    foreign = {"pipelineId": 5, "sourceSystem": "1001", "targetSystem": "1002", "_hostServerId": 1003}
    from config_issues import is_foreign_row
    for disabled in (0, False, "0"):
        rows = [{**outgoing, "_isEnabled": disabled}, {**foreign, "_isEnabled": disabled}]
        assert orphan_pipeline_issues(config, rows) == [] and orphan_pipeline_ids(config, rows) == set()
        assert not is_foreign_row(rows[1])
    for enabled in (1, True, None):
        rows = [{**outgoing, "_isEnabled": enabled}, {**foreign, "_isEnabled": enabled}]
        assert {i.code for i in orphan_pipeline_issues(config, rows)} == {
            "pipeline_references_unknown_server", "pipeline_names_other_server",
        }
        assert orphan_pipeline_ids(config, rows) == {2}


def test_enabling_a_pipeline_raises_the_issue_on_the_next_evaluation(config_dict):
    from config_issues import orphan_pipeline_issues

    config = _orphan_config(config_dict)
    row = {"pipelineId": 2, "sourceSystem": "1001", "targetSystem": "2001", "_hostServerId": 1001, "_isEnabled": 0}
    assert orphan_pipeline_issues(config, [row]) == []
    assert orphan_pipeline_issues(config, [{**row, "_isEnabled": 1}])


def test_every_platform_pipeline_query_already_excludes_disabled_rows():
    from _db.queries import MSSQLQueries, MySQLQueries, PostgreSQLQueries

    for cls in (MySQLQueries, PostgreSQLQueries, MSSQLQueries):
        sql = cls().pipelines_with_metrics().sql if cls is not PostgreSQLQueries else cls().pipelines_with_metrics().sql
        assert "is_enabled" in sql.split("WHERE")[-1].lower().replace("isenabled", "is_enabled"), cls.__name__


def _bus_config(config_dict, used=(1,)):
    config_dict["service_busses"] = [_bus(1), _bus(2)]
    config_dict["service_busses"][0]["topic"] = "sync"
    config_dict["service_busses"][1]["topic"] = "sync-2"
    node = _database_node("n1", 4001, list(used))
    config_dict["machines"][0].update(machine_id=4, machine_name="Tabish-pc", nodes=[node])
    node["node_name"] = "MySQL 1"
    return RelayConfig.model_validate(config_dict)


def _topic_issues(config, rows):
    from config_issues import orphan_pipeline_issues

    return [i for i in orphan_pipeline_issues(config, rows) if i.code == "pipeline_topic_not_on_used_bus"]


def test_topic_on_a_configured_bus_the_node_does_not_use(config_dict):
    config = _bus_config(config_dict)
    row = {"pipelineId": 4, "sourceSystem": "4001", "targetSystem": "4001", "_hostServerId": 4001, "_queueOrTopic": "sync-2"}
    [issue] = _topic_issues(config, [row])
    assert issue.severity == "error"
    assert issue.message == (
        "Tabish-pc (4) → MySQL 1 (4001): pipeline 4 uses topic 'sync-2' (bus 2), but this service only uses bus 1 "
        "(topic 'sync'). Add bus 2 to its used_buses or change queue_or_topic."
    )
    assert issue.where["pipelineIds"] == [4] and issue.where["topic"] == "sync-2"


def test_topic_that_no_configured_bus_provides(config_dict):
    config = _bus_config(config_dict)
    row = {"pipelineId": 4, "sourceSystem": "4001", "targetSystem": "4001", "_hostServerId": 4001, "_queueOrTopic": "ghost"}
    [issue] = _topic_issues(config, [row])
    assert issue.message == (
        "Tabish-pc (4) → MySQL 1 (4001): pipeline 4 uses topic 'ghost', which no configured service bus provides. "
        "Fix queue_or_topic or configure the bus."
    )


def test_topics_on_used_buses_and_missing_topics_are_fine_and_disabled_rows_are_silent(config_dict):
    config = _bus_config(config_dict, used=(1, 2))
    base = {"pipelineId": 4, "sourceSystem": "4001", "targetSystem": "4001", "_hostServerId": 4001}
    assert _topic_issues(config, [{**base, "_queueOrTopic": "sync"}, {**base, "_queueOrTopic": "sync-2"}, {**base}]) == []
    narrow = _bus_config({**config_dict}, used=(1,))
    assert _topic_issues(narrow, [{**base, "_queueOrTopic": "sync-2", "_isEnabled": 0}]) == []
    assert _topic_issues(narrow, [{**base, "_queueOrTopic": "sync-2", "_isEnabled": 1}])


def test_node_with_no_used_bus_and_precedence_one_issue_per_row(config_dict):
    from config_issues import orphan_pipeline_issues

    config = _bus_config(config_dict, used=())
    base = {"pipelineId": 4, "sourceSystem": "4001", "targetSystem": "4001", "_hostServerId": 4001, "_queueOrTopic": "sync"}
    [issue] = _topic_issues(config, [base])
    assert "this service uses no bus" in issue.message
    foreign = {**base, "sourceSystem": "1", "targetSystem": "2"}
    unknown = {**base, "pipelineId": 5, "targetSystem": "9999"}
    codes = [i.code for i in orphan_pipeline_issues(config, [foreign, unknown])]
    assert sorted(codes) == ["pipeline_names_other_server", "pipeline_references_unknown_server"]


def test_topic_error_rows_are_excluded_by_host_and_pipeline_id_only(config_dict):
    from config_issues import excluded_row_keys

    config = _bus_config(config_dict)
    good = {"pipelineId": 4, "sourceSystem": "4001", "targetSystem": "4001", "_hostServerId": 4001, "_queueOrTopic": "sync"}
    bad = {**good, "_hostServerId": 4002, "_queueOrTopic": "sync-2"}
    assert excluded_row_keys(config, [good]) == set()
    assert excluded_row_keys(config, [good, {**good, "_queueOrTopic": "sync-2"}]) == {(4001, 4)}


def test_orphan_messages_name_the_mssql_pipeline_table_for_an_mssql_host(config_dict):
    from config_issues import orphan_pipeline_issues

    node = _database_node("n1", 1001, used_buses=[])
    node["node_platform"] = "mssql"
    node["node_config"]["port"] = 1433
    config_dict["machines"][0]["nodes"] = [node]
    config = RelayConfig.model_validate(config_dict)
    missing = {"pipelineId": 7, "sourceSystem": "1001", "targetSystem": "2001", "_hostServerId": 1001}
    foreign = {"pipelineId": 8, "sourceSystem": "2001", "targetSystem": "3001", "_hostServerId": 1001}
    messages = [i.message for i in orphan_pipeline_issues(config, [missing, foreign])]
    assert any("Remove it from SyncPipeline or add server 2001" in m for m in messages)
    assert any("Fix SourceSystem/TargetSystem in SyncPipeline." in m for m in messages)
    assert not any("sync_pipeline" in m for m in messages)
