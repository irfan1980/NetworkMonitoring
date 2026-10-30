from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from _db.mssql_odbc import MSSQLOdbcDriver, MssqlOdbcError, _FULL_BATCH_FAILURES_BEFORE_COOLDOWN


class FakeServer:
    id = 1004
    platform = "mssql"
    driver = "odbc"
    host = "127.0.0.1"
    port = 1433
    user = "sync_user_odbc"
    password = "SuperSecretPassword123!"
    database = "mssql_sync_odbc"
    schema_name = None
    timeout_ms = None


class FakeSettings:
    query_timeout_ms_default = 4000
    query_timeout_ms_odbc = 8000
    tcp_timeout_ms = 2500
    probe_timeout_ms_odbc = 6000
    auth_probe_interval_ms = 30000
    inline_query_max_chars = 4000
    mssql_in_chunk_size = 1000


def _driver() -> MSSQLOdbcDriver:
    d = MSSQLOdbcDriver(FakeServer(), FakeSettings())
    d.begin_poll_cycle()
    return d


def test_password_never_appears_in_argv(monkeypatch):
    captured = {}

    def fake_run(argv, env, capture_output, text, timeout):
        captured["argv"] = argv
        captured["env"] = env
        return SimpleNamespace(returncode=0, stdout="[]\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    d = _driver()
    d._run_sqlcmd("SELECT 1")

    argv_text = " ".join(captured["argv"])
    assert FakeServer.password not in argv_text
    assert "-P" not in captured["argv"]


def test_password_delivered_via_sqlcmdpassword_env_var(monkeypatch):
    captured = {}

    def fake_run(argv, env, capture_output, text, timeout):
        captured["env"] = env
        return SimpleNamespace(returncode=0, stdout="[]\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    d = _driver()
    d._run_sqlcmd("SELECT 1")

    assert captured["env"]["SQLCMDPASSWORD"] == FakeServer.password


def test_password_never_appears_in_any_error_string_p119(monkeypatch):
    password = FakeServer.password

    def fake_run_generic_failure(argv, env, capture_output, text, timeout):
        return SimpleNamespace(returncode=1, stdout="", stderr="Login failed for user 'sync_user_odbc'.")

    monkeypatch.setattr(subprocess, "run", fake_run_generic_failure)
    d = _driver()
    with pytest.raises(MssqlOdbcError) as excinfo:
        d._run_sqlcmd("SELECT 1")
    assert password not in str(excinfo.value)
    assert "Login failed" in str(excinfo.value)


def test_password_never_appears_on_a_timeout_error(monkeypatch):
    def fake_run_timeout(argv, env, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    monkeypatch.setattr(subprocess, "run", fake_run_timeout)
    d = _driver()
    with pytest.raises(MssqlOdbcError) as excinfo:
        d._run_sqlcmd("SELECT 1")
    assert FakeServer.password not in str(excinfo.value)


def test_error_message_is_built_strictly_from_stderr_not_from_exception_message(monkeypatch):

    def fake_run(argv, env, capture_output, text, timeout):
        return SimpleNamespace(returncode=1, stdout="", stderr="  Invalid object name 'dbo.sync_pipelinex'.  \n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    d = _driver()
    with pytest.raises(MssqlOdbcError) as excinfo:
        d._run_sqlcmd("SELECT * FROM dbo.sync_pipelinex")
    assert str(excinfo.value) == "Invalid object name 'dbo.sync_pipelinex'."


def test_isolation_prefix_on_a_plain_query(monkeypatch):
    captured = {}

    def fake_run(argv, env, capture_output, text, timeout):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="[]\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    d = _driver()
    d._run_sqlcmd("SELECT 1")

    sql_arg = captured["argv"][captured["argv"].index("-Q") + 1]
    assert sql_arg.startswith("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;\n")


def test_isolation_prefix_on_the_temp_file_fallback_path(monkeypatch, tmp_path):
    written = {}
    real_mkstemp = __import__("tempfile").mkstemp

    def fake_run(argv, env, capture_output, text, timeout):
        idx = argv.index("-i")
        path = argv[idx + 1]
        written["text"] = open(path, encoding="utf-8").read()
        return SimpleNamespace(returncode=0, stdout="[]\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    d = _driver()
    long_sql = "SELECT 1 -- " + ("x" * 5000)
    d._run_sqlcmd(long_sql)

    assert written["text"].startswith("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;\n")


def test_there_is_no_lower_level_primitive_that_skips_isolation():
    import inspect

    from _db import mssql_odbc

    source = inspect.getsource(mssql_odbc)
    assert source.count("subprocess.run(") == 1


def test_short_query_stays_inline(monkeypatch):
    captured = {}

    def fake_run(argv, env, capture_output, text, timeout):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="[]\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    d = _driver()
    d._run_sqlcmd("SELECT 1")
    assert "-Q" in captured["argv"]
    assert "-i" not in captured["argv"]


def test_long_query_past_inline_max_chars_routes_through_temp_file(monkeypatch):
    captured = {}

    def fake_run(argv, env, capture_output, text, timeout):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="[]\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    d = _driver()
    long_sql = "SELECT 1 -- " + ("x" * (FakeSettings.inline_query_max_chars + 100))
    d._run_sqlcmd(long_sql)
    assert "-i" in captured["argv"]
    assert "-Q" not in captured["argv"]


def test_temp_file_is_cleaned_up_afterward(monkeypatch):
    seen_path = {}

    def fake_run(argv, env, capture_output, text, timeout):
        seen_path["path"] = argv[argv.index("-i") + 1]
        return SimpleNamespace(returncode=0, stdout="[]\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    d = _driver()
    long_sql = "SELECT 1 -- " + ("x" * (FakeSettings.inline_query_max_chars + 100))
    d._run_sqlcmd(long_sql)

    import os

    assert not os.path.exists(seen_path["path"])


def test_calling_a_method_before_begin_poll_cycle_raises():
    d = MSSQLOdbcDriver(FakeServer(), FakeSettings())
    with pytest.raises(RuntimeError):
        d._require_cycle_started()


def test_begin_poll_cycle_unblocks_other_methods():
    d = MSSQLOdbcDriver(FakeServer(), FakeSettings())
    d.begin_poll_cycle()
    d._require_cycle_started()


def test_end_poll_cycle_re_blocks():
    d = MSSQLOdbcDriver(FakeServer(), FakeSettings())
    d.begin_poll_cycle()
    d.end_poll_cycle()
    with pytest.raises(RuntimeError):
        d._require_cycle_started()


async def test_calling_a_public_method_before_begin_poll_cycle_raises_o11():
    d = MSSQLOdbcDriver(FakeServer(), FakeSettings())
    with pytest.raises(RuntimeError, match="begin_poll_cycle"):
        await d.fetch_triggers()


async def test_begin_poll_cycle_unblocks_a_real_public_method_o11(monkeypatch):
    def fake_run(argv, env, capture_output, text, timeout):
        return SimpleNamespace(returncode=0, stdout=_json_line([{"pipeline_id": 101}]), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    d = MSSQLOdbcDriver(FakeServer(), FakeSettings())
    d.begin_poll_cycle()
    result = await d.fetch_triggers()
    assert result == [{"pipeline_id": 101}]


async def test_end_poll_cycle_re_blocks_a_real_public_method_o11():
    d = MSSQLOdbcDriver(FakeServer(), FakeSettings())
    d.begin_poll_cycle()
    d.end_poll_cycle()
    with pytest.raises(RuntimeError, match="begin_poll_cycle"):
        await d.fetch_triggers()


def _json_line(rows) -> str:
    import json

    return json.dumps(rows) if rows else ""


async def test_full_batch_used_when_it_succeeds(monkeypatch):
    def fake_run(argv, env, capture_output, text, timeout):
        sql = argv[argv.index("-Q") + 1] if "-Q" in argv else open(argv[argv.index("-i") + 1]).read()
        assert "SyncTriggers" in sql
        lines = "\n".join([_json_line([{"pipelineId": 101}]), _json_line([{"cnt": 6, "maxId": 106}]), _json_line([{"cnt": 18, "maxId": 18}]), _json_line([{"pipeline_id": 101}])])
        return SimpleNamespace(returncode=0, stdout=lines, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    d = _driver()
    result = await d.fetch_poll_batch()
    assert result["triggers"] is not None
    assert len(result["pipelines"]) == 1


async def test_full_batch_falls_back_to_essentials_after_repeated_failures(monkeypatch):
    calls = {"n": 0}
    tier1_attempted = {"v": False}

    def fake_run(argv, env, capture_output, text, timeout):
        sql = argv[argv.index("-Q") + 1] if "-Q" in argv else open(argv[argv.index("-i") + 1]).read()
        if "SyncTriggers" in sql:
            tier1_attempted["v"] = True
            return SimpleNamespace(returncode=1, stdout="", stderr="simulated failure")
        calls["n"] += 1
        lines = "\n".join([_json_line([{"pipelineId": 101}]), _json_line([{"cnt": 6, "maxId": 106}])])
        return SimpleNamespace(returncode=0, stdout=lines, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    d = _driver()

    for _ in range(_FULL_BATCH_FAILURES_BEFORE_COOLDOWN):
        d.begin_poll_cycle()
        result = await d.fetch_poll_batch()
        assert result["triggers"] is None
    assert tier1_attempted["v"] is True

    calls["n"] = 0
    tier1_attempted["v"] = False
    d.begin_poll_cycle()
    result = await d.fetch_poll_batch()
    assert result["triggers"] is None
    assert d._full_batch_in_cooldown() is True
    assert tier1_attempted["v"] is False


async def test_essentials_also_failing_falls_all_the_way_to_per_query(monkeypatch):
    def fake_run(argv, env, capture_output, text, timeout):
        sql = argv[argv.index("-Q") + 1] if "-Q" in argv else open(argv[argv.index("-i") + 1]).read()
        if "AS b;" in sql and "AS c;" not in sql:
            return SimpleNamespace(returncode=1, stdout="", stderr="simulated failure")
        if "SyncTriggers" in sql and "AS c;" in sql:
            return SimpleNamespace(returncode=1, stdout="", stderr="simulated failure")
        if "pipelineId" in sql:
            return SimpleNamespace(returncode=0, stdout=_json_line([{"pipelineId": 101}]), stderr="")
        if "cnt, MAX(PipelineId) maxId FROM dbo.SyncPipeline" in sql:
            return SimpleNamespace(returncode=0, stdout=_json_line([{"cnt": 6, "maxId": 106}]), stderr="")
        if "cnt, MAX(PipelineId) maxId FROM dbo.SyncTriggers" in sql:
            return SimpleNamespace(returncode=0, stdout=_json_line([{"cnt": 18, "maxId": 18}]), stderr="")
        return SimpleNamespace(returncode=0, stdout=_json_line([{"pipeline_id": 101}]), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    d = _driver()
    result = await d.fetch_poll_batch()
    assert len(result["pipelines"]) == 1
    assert result["triggers"] is not None and len(result["triggers"]) == 1


async def _fake_tcp_ok_odbc(*args, **kwargs):
    from _metrics.readability import ReadStatus

    return ReadStatus.OK


async def test_check_chain_skips_the_pooled_spawn_when_the_batch_already_ran(monkeypatch):
    from _metrics.readability import ReadStatus
    import _db.mssql_odbc as mssql_odbc_module

    monkeypatch.setattr(mssql_odbc_module, "tcp_check", _fake_tcp_ok_odbc)

    spawn_count = {"n": 0}

    def fake_run(argv, env, capture_output, text, timeout):
        spawn_count["n"] += 1
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    d = _driver()
    d._auth_probe.record(True)
    d._batch_ran_this_cycle = True

    status = await d.check_chain()
    assert status == ReadStatus.OK
    assert spawn_count["n"] == 0


async def test_check_chain_spawns_the_pooled_check_when_the_batch_did_not_run(monkeypatch):
    from _metrics.readability import ReadStatus
    import _db.mssql_odbc as mssql_odbc_module

    monkeypatch.setattr(mssql_odbc_module, "tcp_check", _fake_tcp_ok_odbc)

    spawn_count = {"n": 0}

    def fake_run(argv, env, capture_output, text, timeout):
        spawn_count["n"] += 1
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    d = _driver()
    d._auth_probe.record(True)
    assert d._batch_ran_this_cycle is False

    status = await d.check_chain()
    assert status == ReadStatus.OK
    assert spawn_count["n"] == 1


async def test_batch_ran_this_cycle_flag_is_reset_by_begin_poll_cycle(monkeypatch):
    def fake_run(argv, env, capture_output, text, timeout):
        lines = "\n".join([_json_line([{"pipelineId": 101}]), _json_line([{"cnt": 6, "maxId": 106}]), _json_line([{"cnt": 18, "maxId": 18}]), _json_line([{"pipeline_id": 101}])])
        return SimpleNamespace(returncode=0, stdout=lines, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    d = _driver()
    await d.fetch_poll_batch()
    assert d._batch_ran_this_cycle is True

    d.end_poll_cycle()
    d.begin_poll_cycle()
    assert d._batch_ran_this_cycle is False


async def test_fetch_poll_batch_only_sets_the_flag_on_genuine_success(monkeypatch):
    def fake_run(argv, env, capture_output, text, timeout):
        return SimpleNamespace(returncode=1, stdout="", stderr="simulated failure")

    monkeypatch.setattr(subprocess, "run", fake_run)
    d = _driver()
    with pytest.raises(MssqlOdbcError):
        await d.fetch_poll_batch()
    assert d._batch_ran_this_cycle is False
