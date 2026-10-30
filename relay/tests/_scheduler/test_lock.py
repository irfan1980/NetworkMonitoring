from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

import psutil
import pytest

from _scheduler.lock import InstanceLock
from _scheduler.utc import iso

from .conftest import SCRIPT


@pytest.fixture
def sleeper():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield proc
    proc.kill()
    proc.wait()


def write_lock(path, pid, create_time="auto", token="other"):
    path.parent.mkdir(parents=True, exist_ok=True)
    info = {"pid": pid, "token": token}
    if create_time == "auto":
        info["create_time"] = psutil.Process(pid).create_time()
    elif create_time is not None:
        info["create_time"] = create_time
    path.write_text(json.dumps(info))


def test_acquire_records_pid_create_time_and_marker(tmp_path):
    lock = InstanceLock(tmp_path / "l.json")
    assert lock.acquire()
    info = json.loads((tmp_path / "l.json").read_text())
    assert info["pid"] == os.getpid() and abs(info["create_time"] - psutil.Process().create_time()) < 1
    assert info["cmdline"] and info["token"] == lock.token
    lock.release()
    assert not (tmp_path / "l.json").exists()


def test_live_holder_with_matching_create_time_blocks(tmp_path, sleeper):
    path = tmp_path / "l.json"
    write_lock(path, sleeper.pid)
    assert InstanceLock(path).acquire() is False
    assert json.loads(path.read_text())["token"] == "other"


def test_pid_reuse_with_a_different_create_time_is_stale(tmp_path, sleeper):
    path = tmp_path / "l.json"
    write_lock(path, sleeper.pid, create_time=psutil.Process(sleeper.pid).create_time() - 3600)
    lock = InstanceLock(path)
    assert lock.acquire() is True
    assert json.loads(path.read_text())["pid"] == os.getpid()


def test_create_time_within_tolerance_still_counts_as_the_same_process(tmp_path, sleeper):
    path = tmp_path / "l.json"
    write_lock(path, sleeper.pid, create_time=psutil.Process(sleeper.pid).create_time() + 0.5)
    assert InstanceLock(path).acquire() is False


def test_own_pid_from_a_previous_life_is_stale(tmp_path):
    path = tmp_path / "l.json"
    write_lock(path, os.getpid(), token="previous-life")
    assert InstanceLock(path).acquire() is True


def test_dead_pid_is_stale(tmp_path):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    path = tmp_path / "l.json"
    write_lock(path, dead.pid, create_time=1.0)
    assert InstanceLock(path).acquire() is True


def test_legacy_lock_without_create_time_falls_back_to_pid_exists(tmp_path, sleeper):
    path = tmp_path / "l.json"
    write_lock(path, sleeper.pid, create_time=None)
    assert InstanceLock(path).acquire() is False


def test_corrupt_lock_file_is_stale(tmp_path):
    path = tmp_path / "l.json"
    path.write_text("{")
    assert InstanceLock(path).acquire() is True


def test_acquire_is_idempotent_for_the_holder(tmp_path):
    lock = InstanceLock(tmp_path / "l.json")
    assert lock.acquire() and lock.acquire()


async def test_loop_is_disabled_while_another_process_holds_the_lock(harness, sleeper, caplog):
    write_lock(harness.state_dir / "scheduler" / "scheduler.lock", sleeper.pid)
    with caplog.at_level(logging.INFO, logger="relay.scheduler"):
        assert harness.loop.start() is False
    assert harness.loop.enabled is False
    assert "scheduler disabled (another instance holds the lock)" in caplog.text

    schedule = harness.add(trigger={"kind": "interval", "every_seconds": 1, "start_at": iso(harness.clock.now)})
    await harness.loop.tick()
    harness.clock.advance(seconds=30)
    await harness.loop.tick()
    assert harness.runs(schedule.id) == [] and harness.loop.enabled is False


async def test_disabled_loop_takes_over_when_the_holder_dies(harness, sleeper, caplog):
    path = harness.state_dir / "scheduler" / "scheduler.lock"
    write_lock(path, sleeper.pid)
    harness.loop.start()
    sleeper.kill()
    sleeper.wait()
    with caplog.at_level(logging.INFO, logger="relay.scheduler"):
        await harness.loop.tick()
    assert harness.loop.enabled is True
    assert "scheduler enabled (lock acquired)" in caplog.text
    assert json.loads(path.read_text())["token"] == harness.loop._lock.token

    schedule = harness.add(trigger={"kind": "interval", "every_seconds": 60, "start_at": iso(harness.clock.now)})
    await harness.loop.tick()
    harness.clock.advance(seconds=61)
    await harness.loop.tick()
    await harness.settle()
    assert len(harness.runs(schedule.id)) == 1
    await harness.loop.shutdown()


async def test_takeover_reloads_schedules_and_run_history_written_by_the_old_holder(harness, sleeper):
    write_lock(harness.state_dir / "scheduler" / "scheduler.lock", sleeper.pid)
    harness.loop.start()
    other_store = type(harness.store)(harness.state_dir)
    created = other_store.create(__import__("tests._scheduler.conftest", fromlist=["x"]).schedule_input())
    assert harness.store.get(created.id) is None
    sleeper.kill()
    sleeper.wait()
    await harness.loop.tick()
    assert harness.store.get(created.id) is not None
    await harness.loop.shutdown()


async def test_restart_with_the_same_pid_after_a_hard_kill_reacquires(harness):
    path = harness.state_dir / "scheduler" / "scheduler.lock"
    write_lock(path, os.getpid(), create_time=psutil.Process().create_time(), token="killed-instance")
    assert harness.loop.start() is True
    assert harness.loop.enabled is True
