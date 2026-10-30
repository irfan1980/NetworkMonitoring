from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from tests import _db_lock


@pytest.fixture
def lock_path(tmp_path, monkeypatch):
    path = tmp_path / ".pytest-db.lock"
    monkeypatch.setattr(_db_lock, "LOCK_PATH", path)
    return path


def test_acquire_writes_pid_and_timestamp(lock_path):
    _db_lock.acquire()
    info = json.loads(lock_path.read_text(encoding="utf-8"))
    assert info["pid"] == os.getpid()
    assert "started" in info
    _db_lock.release()


def test_release_removes_the_lock_file(lock_path):
    _db_lock.acquire()
    assert lock_path.exists()
    _db_lock.release()
    assert not lock_path.exists()


def test_second_acquire_by_a_live_process_fails_fast_naming_the_pid(lock_path):
    lock_path.write_text(
        json.dumps({"pid": os.getpid(), "started": datetime.now(timezone.utc).isoformat(), "host": "x"}),
        encoding="utf-8",
    )
    with pytest.raises(_db_lock.DbLockHeld) as exc:
        _db_lock.acquire()
    assert str(os.getpid()) in str(exc.value)


def test_a_lock_held_by_a_dead_pid_is_stolen_not_blocked_forever(lock_path):
    lock_path.write_text(
        json.dumps({"pid": 999999, "started": datetime.now(timezone.utc).isoformat(), "host": "x"}),
        encoding="utf-8",
    )
    _db_lock.acquire()
    info = json.loads(lock_path.read_text(encoding="utf-8"))
    assert info["pid"] == os.getpid()
    _db_lock.release()


def test_a_too_old_lock_is_stolen_even_with_a_technically_live_pid(lock_path):
    old = datetime.now(timezone.utc) - timedelta(seconds=_db_lock.STALE_AFTER_SECONDS + 60)
    lock_path.write_text(
        json.dumps({"pid": os.getpid(), "started": old.isoformat(), "host": "x"}),
        encoding="utf-8",
    )
    _db_lock.acquire()
    _db_lock.release()


def test_release_never_deletes_a_lock_it_does_not_own(lock_path):
    lock_path.write_text(
        json.dumps({"pid": 999999, "started": datetime.now(timezone.utc).isoformat(), "host": "x"}),
        encoding="utf-8",
    )
    _db_lock.release()
    assert lock_path.exists()
