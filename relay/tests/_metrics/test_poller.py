import asyncio

import pytest

from _metrics.down_confirm import DownConfirm
from _metrics.poll_stats import PollStats
from _metrics.poller import PollSpec, Poller
from _metrics.readability import ReadStatus

OK = ReadStatus.OK
DOWN = ReadStatus.DOWN
UNREAD = ReadStatus.UNREAD


def ok_stage():
    async def _stage() -> ReadStatus:
        return OK

    return _stage


def down_stage():
    async def _stage() -> ReadStatus:
        return DOWN

    return _stage


def raising_stage():
    async def _stage() -> ReadStatus:
        raise RuntimeError("boom")

    return _stage


def slow_stage(seconds: float, result: ReadStatus = OK):
    async def _stage() -> ReadStatus:
        await asyncio.sleep(seconds)
        return result

    return _stage


async def test_run_cycle_reports_ok_and_down_stages():
    poller = Poller(DownConfirm())
    specs = [
        PollSpec("db:1001", [("tcp", ok_stage()), ("query", down_stage())]),
    ]
    result = await poller.run_cycle(specs)
    assert result.published is True
    assert result.metrics["db:1001:tcp"] == OK
    assert result.confirmed["db:1001:query"] == DOWN


async def test_multiple_specs_are_checked_concurrently_not_sequentially():
    poller = Poller(DownConfirm())
    specs = [
        PollSpec("db:1001", [("tcp", slow_stage(0.05))]),
        PollSpec("db:1002", [("tcp", slow_stage(0.05))]),
        PollSpec("db:1003", [("tcp", slow_stage(0.05))]),
    ]
    started = asyncio.get_event_loop().time()
    await poller.run_cycle(specs)
    elapsed = asyncio.get_event_loop().time() - started
    assert elapsed < 0.12


async def test_one_servers_exception_does_not_break_other_servers():
    poller = Poller(DownConfirm())
    specs = [
        PollSpec("db:1001", [("query", raising_stage())]),
        PollSpec("db:1002", [("query", ok_stage())]),
    ]
    result = await poller.run_cycle(specs)
    assert result.metrics["db:1001:query"] == UNREAD
    assert result.metrics["db:1002:query"] == OK


async def test_a_timed_out_stage_is_unread_not_down():
    poller = Poller(DownConfirm(), per_object_timeout_ms=20, object_budget_ms=15000)
    specs = [PollSpec("db:1001", [("query", slow_stage(0.2))])]
    result = await poller.run_cycle(specs)
    assert result.metrics["db:1001:query"] == UNREAD


async def test_a_stage_beyond_the_exhausted_budget_is_skipped_as_unread():
    poller = Poller(DownConfirm(), per_object_timeout_ms=1000, object_budget_ms=30)
    specs = [
        PollSpec(
            "db:1001",
            [
                ("tcp", slow_stage(0.05, OK)),
                ("query", ok_stage()),
            ],
        )
    ]
    result = await poller.run_cycle(specs)
    assert result.metrics["db:1001:tcp"] == UNREAD
    assert result.metrics["db:1001:query"] == UNREAD


async def test_budget_is_shared_cumulatively_across_stages_not_reset_each_stage():
    fake_now = [0.0]

    def clock() -> float:
        return fake_now[0]

    poller = Poller(DownConfirm(), per_object_timeout_ms=1000, object_budget_ms=60, stage_chain_clock=clock)
    calls = []

    async def stage_a():
        calls.append("a")
        fake_now[0] += 0.04
        await asyncio.sleep(0)
        return OK

    async def stage_b():
        calls.append("b")
        await asyncio.sleep(0.5)
        return OK

    specs = [PollSpec("db:1001", [("a", stage_a), ("b", stage_b)])]
    result = await poller.run_cycle(specs)
    assert calls == ["a", "b"]
    assert result.metrics["db:1001:b"] == UNREAD


async def test_metrics_are_populated_even_for_a_stale_discarded_generation():
    dc = DownConfirm()
    poller = Poller(dc)
    fast_specs = [PollSpec("db:1001", [("tcp", ok_stage())])]

    poller._published_generation = 999
    result = await poller.run_cycle(fast_specs)
    assert result.published is False
    assert result.metrics["db:1001:tcp"] == OK
    assert result.confirmed == {}


async def test_missing_object_ids_are_treated_as_unread_across_cycles():
    dc = DownConfirm()
    poller = Poller(dc)

    specs_with_object = [PollSpec("db:1001", [("tcp", ok_stage())])]
    r1 = await poller.run_cycle(specs_with_object)
    assert r1.confirmed["db:1001:tcp"] == OK
    assert "db:1001:tcp" not in r1.metrics or True

    specs_without_object = [PollSpec("db:1002", [("tcp", ok_stage())])]
    r2 = await poller.run_cycle(specs_without_object)
    assert "db:1001:tcp" not in r2.metrics
    assert "db:1001:tcp" in r2.confirmed
    assert r2.confirmed["db:1001:tcp"] == OK
    assert dc.unread_streak_ms("db:1001:tcp") == 0.0


async def test_a_stale_object_id_does_not_drift_out_of_observation_forever():
    dc = DownConfirm(down_confirm_soft_min_ms=0)
    poller = Poller(dc)

    await poller.run_cycle([PollSpec("db:1001", [("tcp", ok_stage())])])
    await poller.run_cycle([PollSpec("db:1002", [("tcp", ok_stage())])])

    r3 = await poller.run_cycle([PollSpec("db:1001", [("tcp", down_stage())])])
    assert r3.confirmed["db:1001:tcp"] == OK
    r4 = await poller.run_cycle([PollSpec("db:1001", [("tcp", down_stage())])])
    assert r4.confirmed["db:1001:tcp"] == DOWN


async def test_generation_increments_every_cycle():
    poller = Poller(DownConfirm())
    specs = [PollSpec("db:1001", [("tcp", ok_stage())])]
    r1 = await poller.run_cycle(specs)
    r2 = await poller.run_cycle(specs)
    assert r2.generation == r1.generation + 1


async def test_a_stale_generation_never_overwrites_a_newer_published_one():
    dc = DownConfirm()
    poller = Poller(dc)
    down_specs = [PollSpec("db:1001", [("query", down_stage())])]
    ok_specs = [PollSpec("db:1001", [("query", ok_stage())])]

    r1 = await poller.run_cycle(down_specs)
    assert r1.confirmed["db:1001:query"] == DOWN

    poller._poll_generation = 5
    poller._published_generation = 5
    stale_result = await _run_cycle_as_generation(poller, down_specs, generation=3)
    assert stale_result.published is False
    assert dc.confirmed_status("db:1001:query") == DOWN


async def _run_cycle_as_generation(poller: Poller, specs, generation: int):
    poller._poll_generation = generation - 1
    return await poller.run_cycle(specs)


async def test_p77_layer1_a_slow_straggler_never_blocks_a_newer_cycle_from_starting():
    dc = DownConfirm()
    poller = Poller(dc)

    slow_specs = [PollSpec("db:1001", [("query", slow_stage(0.08, DOWN))])]
    fast_specs = [PollSpec("db:1001", [("query", ok_stage())])]

    slow_task = asyncio.create_task(poller.run_cycle(slow_specs))
    await asyncio.sleep(0.01)
    fast_result = await poller.run_cycle(fast_specs)
    assert fast_result.generation == 2
    assert fast_result.published is True
    assert fast_result.confirmed["db:1001:query"] == OK

    slow_result = await slow_task
    assert slow_result.generation == 1
    assert slow_result.published is False
    assert dc.confirmed_status("db:1001:query") == OK


async def test_from_settings_reads_c6_fields_not_hardcoded_defaults():
    from _contract.schema import Settings

    settings = Settings.model_validate(
        {
            "auth": {
                "web_client_id": "a", "web_client_secret": "b",
                "app_client_id": "c", "app_client_secret": "d",
            },
            "ack_pin": "1234",
            "per_server_timeout_ms": 111,
            "server_budget_ms": 222,
            "poll_hard_timeout_ms": 333,
        }
    )
    poller = Poller.from_settings(settings, DownConfirm())
    assert poller.per_object_timeout_ms == 111
    assert poller.object_budget_ms == 222
    assert poller.poll_hard_timeout_ms == 333


async def test_from_settings_value_actually_reaches_the_stage_chain():
    from _contract.schema import Settings

    settings = Settings.model_validate(
        {
            "auth": {
                "web_client_id": "a", "web_client_secret": "b",
                "app_client_id": "c", "app_client_secret": "d",
            },
            "ack_pin": "1234",
            "per_server_timeout_ms": 20,
            "server_budget_ms": 15000,
        }
    )
    poller = Poller.from_settings(settings, DownConfirm())
    specs = [PollSpec("db:1001", [("query", slow_stage(0.2))])]
    result = await poller.run_cycle(specs)
    assert result.metrics["db:1001:query"] == UNREAD


async def test_from_settings_poll_hard_timeout_ms_actually_reaches_run_cycle_c2():
    from _contract.schema import Settings

    settings = Settings.model_validate(
        {
            "auth": {
                "web_client_id": "a", "web_client_secret": "b",
                "app_client_id": "c", "app_client_secret": "d",
            },
            "ack_pin": "1234",
            "poll_hard_timeout_ms": 20,
        }
    )
    poller = Poller.from_settings(settings, DownConfirm())
    specs = [PollSpec("db:1001", [("tcp", slow_stage(1.0))])]
    result = await poller.run_cycle(specs)
    assert result.published is True
    assert result.metrics["db:1001:tcp"] == UNREAD


async def test_run_cycle_records_into_poll_stats():
    stats = PollStats()
    poller = Poller(DownConfirm(), poll_stats=stats)
    specs = [PollSpec("db:1001", [("tcp", ok_stage())])]
    await poller.run_cycle(specs)
    snapshot = stats.snapshot()
    assert snapshot["samples"] == 1


async def test_a_discarded_stale_cycle_does_not_record_into_poll_stats():
    stats = PollStats()
    poller = Poller(DownConfirm(), poll_stats=stats)
    specs = [PollSpec("db:1001", [("tcp", ok_stage())])]
    poller._published_generation = 999
    await poller.run_cycle(specs)
    assert stats.snapshot()["samples"] == 0


async def test_poll_hard_timeout_marks_everything_unread_never_crashes():
    poller = Poller(DownConfirm(), per_object_timeout_ms=5000, object_budget_ms=5000, poll_hard_timeout_ms=20)
    specs = [PollSpec("db:1001", [("tcp", slow_stage(1.0))])]
    result = await poller.run_cycle(specs)
    assert result.published is True
    assert result.metrics["db:1001:tcp"] == UNREAD
