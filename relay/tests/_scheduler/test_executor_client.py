from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from _contract.schema import Machine, SchedulerSettings
from _scheduler.exec_key import exec_key
from _scheduler.executor_client import (
    EndpointAuthRejected, EndpointUnavailable, ExecutorClient,
)

from .fake_endpoint import FakeEndpoint

MACHINE = Machine(machine_id="0004", machine_ip="10.101.8.85", machine_name="m", machine_type="t", machine_os="Windows")
NOW = 1_800_000_000.0


def make(endpoint: FakeEndpoint, clock=lambda: NOW):
    return ExecutorClient(lambda: SchedulerSettings(), transport=httpx.MockTransport(endpoint.handler), clock=clock)


def endpoint() -> FakeEndpoint:
    ep = FakeEndpoint(4, "10.101.8.85", clock=lambda: NOW)
    ep.add_script(r"D:\jobs\ok.cmd", polls=1, stdout="hi")
    return ep


def test_key_is_sha512_of_id_bracket_ip():
    bracket = int(NOW) // 900
    expected = hashlib.sha512(f"4_{bracket}_10.101.8.85".encode()).hexdigest()
    assert exec_key(4, "10.101.8.85", NOW) == expected
    assert exec_key(int("0004"), "10.101.8.85", NOW - (NOW % 900) + 899) == expected
    assert exec_key(4, "10.101.8.85", NOW + 900) != expected


async def test_requests_go_to_ip_and_port_with_the_key_header():
    ep = endpoint()
    await make(ep).health(MACHINE)
    request = ep.requests[0]
    assert str(request.url) == "http://10.101.8.85:8095/health"
    assert request.headers["x-exec-key"] == exec_key(4, "10.101.8.85", NOW)


async def test_previous_bracket_key_is_what_the_fake_accepts_and_two_old_is_not():
    ep = endpoint()
    await make(ep, clock=lambda: NOW - 900).health(MACHINE)
    with pytest.raises(EndpointAuthRejected):
        await make(ep, clock=lambda: NOW - 1800).health(MACHINE)


async def test_health_ok_and_contract_body():
    data = await make(endpoint()).health(MACHINE)
    assert data["ok"] is True and data["machine_id"] == 4


async def test_401_is_auth_rejected_and_403_is_forbidden_ip():
    ep = endpoint()
    ep.reject_keys = True
    with pytest.raises(EndpointAuthRejected) as exc:
        await make(ep).health(MACHINE)
    assert not exc.value.forbidden_ip
    ep.reject_keys, ep.forbid = False, True
    with pytest.raises(EndpointAuthRejected) as exc:
        await make(ep).health(MACHINE)
    assert exc.value.forbidden_ip


async def test_connection_failure_and_5xx_are_unavailable():
    ep = endpoint()
    ep.down = True
    with pytest.raises(EndpointUnavailable):
        await make(ep).health(MACHINE)
    client = ExecutorClient(lambda: SchedulerSettings(), transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(EndpointUnavailable):
        await client.health(MACHINE)


async def test_check_script():
    client = make(endpoint())
    assert await client.check_script(MACHINE, r"D:\jobs\ok.cmd") == {"exists": True, "is_file": True}
    assert await client.check_script(MACHINE, r"D:\nope.cmd") == {"exists": False, "is_file": False}


async def test_start_run_sends_the_contract_body_and_returns_running():
    ep = endpoint()
    result = await make(ep).start_run(MACHINE, "r1", r"D:\jobs\ok.cmd", ["x"], 30, 64)
    assert result.kind == "running"
    start = next(r for r in ep.requests if r.url.path == "/runs")
    assert json.loads(start.content) == {
        "run_id": "r1", "script": r"D:\jobs\ok.cmd", "args": ["x"], "timeout_s": 30, "output_tail_kb": 64,
    }


async def test_start_run_script_not_found_and_locked():
    ep = endpoint()
    client = make(ep)
    assert (await client.start_run(MACHINE, "r0", r"D:\missing.cmd", [], 30, 64)).kind == "script_not_found"
    await client.start_run(MACHINE, "r1", r"D:\jobs\ok.cmd", [], 30, 64)
    locked = await client.start_run(MACHINE, "r2", r"D:\jobs\ok.cmd", [], 30, 64)
    assert locked.kind == "locked" and locked.locked_by == "r1"


async def test_repeating_a_run_id_is_idempotent():
    client = make(endpoint())
    await client.start_run(MACHINE, "r1", r"D:\jobs\ok.cmd", [], 30, 64)
    again = await client.start_run(MACHINE, "r1", r"D:\jobs\ok.cmd", [], 30, 64)
    assert again.kind == "running"


async def test_get_run_progress_unknown_and_finished_shape():
    ep = endpoint()
    client = make(ep)
    assert await client.get_run(MACHINE, "nope") is None
    await client.start_run(MACHINE, "r1", r"D:\jobs\ok.cmd", [], 30, 64)
    assert (await client.get_run(MACHINE, "r1"))["status"] == "running"
    done = await client.get_run(MACHINE, "r1")
    assert done["status"] == "finished" and done["exit_code"] == 0 and done["stdout_tail"] == "hi"


async def test_kill_hits_the_kill_route():
    ep = endpoint()
    client = make(ep)
    await client.start_run(MACHINE, "r1", r"D:\jobs\ok.cmd", [], 30, 64)
    await client.kill(MACHINE, "r1")
    assert ep.requests[-1].method == "POST" and ep.requests[-1].url.path == "/runs/r1/kill"


async def test_extra_keys_in_endpoint_answers_are_tolerated():
    ep = endpoint()
    ep.add_script(r"D:\jobs\ok.cmd", polls=0, script_path=r"C:\x\ok.cmd", interpreter="cmd.exe")
    client = make(ep)
    assert (await client.health(MACHINE))["ok"]
    assert (await client.start_run(MACHINE, "r1", r"D:\jobs\ok.cmd", [], 0, 64)).kind == "running"
    run = await client.get_run(MACHINE, "r1")
    assert run["status"] == "finished" and run["script_path"] == r"C:\x\ok.cmd" and run["interpreter"] == "cmd.exe"


async def test_check_script_passes_resolved_and_error_through():
    ep = endpoint()
    ep.add_script(r"D:\jobs\ok.cmd", script_path=r"C:\x\ok.cmd")
    ep.rejections["bad.cmd"] = {"error": "outside_scripts_dir"}
    client = make(ep)
    assert await client.check_script(MACHINE, r"D:\jobs\ok.cmd") == {"exists": True, "is_file": True, "resolved": r"C:\x\ok.cmd"}
    assert await client.check_script(MACHINE, "bad.cmd") == {"exists": False, "is_file": False, "error": "outside_scripts_dir"}
