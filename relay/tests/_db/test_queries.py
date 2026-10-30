from _db.queries import MSSQLQueries, MySQLQueries, PostgreSQLQueries


def test_s1_mysql_has_no_schema_qualifier():
    q = MySQLQueries()
    assert "sync_pipeline" in q.pipelines_with_metrics().sql
    assert "." not in q.pipelines_with_metrics().sql.split("FROM ")[-1].split()[0].rstrip(";")


def test_s1_postgresql_is_sync_schema_qualified_by_default():
    q = PostgreSQLQueries()
    assert q.schema == "sync"
    sql = q.registry_signature().sql
    assert "sync.sync_pipeline" in sql


def test_s1_postgresql_honours_a_configured_schema_name():
    q = PostgreSQLQueries(schema="custom_schema")
    assert "custom_schema.sync_pipeline" in q.registry_signature().sql


def test_s1_mssql_is_hard_coded_dbo_prefixed():
    q = MSSQLQueries()
    assert "dbo.SyncPipeline" in q.registry_signature().sql
    assert "dbo.SyncOutbox" in q.outbox_sent(150).sql


def test_s1_mssql_queries_class_has_no_schema_parameter_at_all():
    import inspect

    assert "schema" not in inspect.signature(MSSQLQueries.__init__).parameters


def test_s2_mssql_select_1_has_alias_ok():
    assert MSSQLQueries().select_1().sql == "SELECT 1 AS ok"


def test_s3_pipelines_with_metrics_column_list_matches_across_all_three_platforms():
    expected_aliases = {
        "pipelineId",
        "sourceSystem",
        "targetSystem",
        "entityType",
        "queueOrTopic",
        "isEnabled",
        "lastPingAt",
        "heartbeatAgeSeconds",
        "pendingOutbox",
        "failedOutbox",
        "pendingInbox",
        "unresolvedInbox",
        "sendLagSeconds",
        "applyLagSeconds",
        "oldestPendingOutboxSeconds",
        "oldestPendingInboxSeconds",
    }
    for q in (MySQLQueries(), PostgreSQLQueries(), MSSQLQueries()):
        sql = q.pipelines_with_metrics().sql
        for alias in expected_aliases:
            assert alias in sql, f"{q.platform} missing alias {alias}"


def test_s3_failed_outbox_and_unresolved_inbox_carry_no_age_window_p15():
    for q in (MySQLQueries(), PostgreSQLQueries(), MSSQLQueries()):
        sql = q.pipelines_with_metrics().sql
        failed_clause = sql.split("failedOutbox")[0].split("(SELECT COUNT")[-1]
        assert "created_at >=" not in failed_clause and "sent_at >=" not in failed_clause
        unresolved_clause = sql.split("unresolvedInbox")[0].split("(SELECT COUNT")[-1]
        assert "received_at >=" not in unresolved_clause and "applied_at >=" not in unresolved_clause


def test_s4_registry_signature_is_count_and_max_pipeline_id():
    for q in (MySQLQueries(), PostgreSQLQueries(), MSSQLQueries()):
        sql = q.registry_signature().sql
        assert "COUNT(*)" in sql
        assert "MAX(PipelineId)" in sql or "MAX(pipeline_id)" in sql


def test_s5_triggers_is_a_full_unfiltered_table_scan():
    for q in (MySQLQueries(), PostgreSQLQueries(), MSSQLQueries()):
        sql = q.triggers().sql
        assert "WHERE" not in sql
        for col in ("pipeline_id", "entity", "direction", "operation", "function", "format", "severity"):
            assert col in sql


def test_s6_outbox_sent_filters_status_sent_and_a_window():
    for q in (MySQLQueries(), PostgreSQLQueries()):
        query = q.outbox_sent(150)
        assert "status='Sent'" in query.sql
        assert 150 in query.params


def test_s6_mssql_odbc_variant_string_interpolates_instead_of_binding():
    query = MSSQLQueries().outbox_sent_literal(150)
    assert "150" in query.sql
    assert query.params == ()


def test_s7_entity_agg_filters_by_real_pipeline_ids_never_a_message_id_prefix():
    q = MySQLQueries()
    query = q.outbox_entity_agg((101, 102))
    assert "pipeline_id IN" in query.sql
    assert query.params == (101, 102)
    assert "message_id" not in query.sql


def test_s8_mysql_never_chunks_inbox_index():
    query = MySQLQueries().inbox_index(tuple(f"m{i}" for i in range(1500)))
    assert query.sql.count("IN (") == 1
    assert len(query.params) == 1500


def test_s8_mssql_tedious_chunks_at_1000_c33():
    chunks = MSSQLQueries().inbox_index_chunks(tuple(f"m{i}" for i in range(2500)), chunk_size=1000)
    assert len(chunks) == 3
    assert len(chunks[0].params) == 1000
    assert len(chunks[1].params) == 1000
    assert len(chunks[2].params) == 500


def test_s8_mssql_odbc_does_not_chunk_builds_one_escaped_literal_list():
    query = MSSQLQueries().inbox_index_unchunked(("m1", "m2's tricky"))
    assert query.params == ()
    assert "m2''s tricky" in query.sql


def test_s9_inbox_since_windows_on_received_at_not_applied_at():
    for q in (MySQLQueries(), PostgreSQLQueries(), MSSQLQueries()):
        sql = q.inbox_since(150).sql.lower().replace("_", "")
        assert "receivedat >=" in sql
        assert "appliedat >=" not in sql


def test_s10_error_rows_is_union_all_outbox_failed_and_inbox_error_failed_tagged():
    for q in (MySQLQueries(), PostgreSQLQueries(), MSSQLQueries()):
        sql = q.error_rows(100).sql
        assert "UNION ALL" in sql
        assert "outbox:" in sql
        assert "inbox:" in sql
        assert "ORDER BY at DESC" in sql


def test_s10_mssql_odbc_batch_variant_caps_at_query_layer_via_row_number():
    sql = MSSQLQueries().error_rows_batch_partitioned(20).sql
    assert "ROW_NUMBER() OVER (PARTITION BY" in sql
    assert "rn <= 20" in sql


def test_s11_mysql_filters_by_routine_schema():
    query = MySQLQueries().existing_routine_names("mysql_test_for_service_bus")
    assert "ROUTINE_SCHEMA=%s" in query.sql
    assert query.params == ("mysql_test_for_service_bus",)


def test_s11_postgresql_excludes_system_namespaces_not_a_schema_param():
    sql = PostgreSQLQueries().existing_routine_names().sql
    assert "pg_catalog" in sql and "information_schema" in sql
    assert "nspname =" not in sql


def test_s11_mssql_has_no_schema_filter_at_all_both_drivers_share_this():
    sql = MSSQLQueries().existing_routine_names().sql
    assert "WHERE" not in sql
    assert "INFORMATION_SCHEMA.ROUTINES" in sql


def _all_query_sql_texts() -> list[tuple[str, str]]:
    import inspect

    dummy_args = {
        "scan_window_seconds": 150,
        "window_seconds": 150,
        "pipeline_ids": (101, 102),
        "message_ids": ("1001:ORDER:K:1:p101:uuid",),
        "limit": 100,
        "routine_schema": "sync",
        "chunk_size": 1000,
        "per_pipeline_limit": 20,
    }
    texts: list[tuple[str, str]] = []
    for cls in (MySQLQueries, PostgreSQLQueries, MSSQLQueries):
        instance = cls()
        for name, method in inspect.getmembers(instance, predicate=inspect.ismethod):
            if name.startswith("_"):
                continue
            sig = inspect.signature(method)
            kwargs = {}
            for param_name, param in sig.parameters.items():
                if param.default is not inspect.Parameter.empty:
                    continue
                if param_name not in dummy_args:
                    raise AssertionError(
                        f"{cls.__name__}.{name}: no dummy value registered for "
                        f"required parameter {param_name!r} -- add one to dummy_args "
                        f"above so this new method is genuinely exercised, not skipped."
                    )
                kwargs[param_name] = dummy_args[param_name]
            result = method(**kwargs)
            if isinstance(result, list):
                for i, q in enumerate(result):
                    texts.append((f"{cls.__name__}.{name}[{i}]", q.sql))
            else:
                texts.append((f"{cls.__name__}.{name}", result.sql))
    return texts


def test_p14_dead_lettered_never_appears_in_any_generated_sql_any_platform():
    texts = _all_query_sql_texts()
    assert len(texts) >= 20
    offenders = [label for label, sql in texts if "DeadLettered" in sql]
    assert offenders == [], f"DeadLettered leaked into: {offenders}"


def _all_mssql_sql() -> list[str]:
    q = MSSQLQueries()
    queries = [
        q.registry_signature(), q.registry_signature_triggers(), q.pipelines_with_metrics(), q.triggers(),
        q.outbox_sent(150), q.outbox_sent_literal(150), q.outbox_entity_agg((1,)), q.inbox_entity_agg((1,)),
        q.inbox_entity_last_error((1,)), q.inbox_index_unchunked(("m",)), q.inbox_since(150), q.error_rows(5),
        q.error_rows_batch_partitioned(5), *q.inbox_index_chunks(("m",)),
    ]
    return [x.sql for x in queries]


def test_mssql_queries_use_pascal_case_tables_and_no_sync_underscore_names():
    for sql in _all_mssql_sql():
        assert "sync_" not in sql, sql
    joined = " ".join(_all_mssql_sql())
    for table in ("dbo.SyncPipeline", "dbo.SyncTriggers", "dbo.SyncOutbox", "dbo.SyncInbox"):
        assert table in joined


def test_mssql_triggers_select_function_name_and_payload_format_aliased_to_the_shared_keys():
    sql = MSSQLQueries().triggers().sql
    assert "FunctionName AS [function]" in sql
    assert "PayloadFormat AS format" in sql
    assert "TriggerId" not in sql
