from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import stat
import time
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from _contract.schema import StateBackupSettings
from _state import backup as bk
from _state.database import Database

DAY = date(2026, 11, 2)


class Clock:
    def __init__(self, day=DAY):
        self.day = day

    def __call__(self):
        return self.day


def make(tmp_path, clock=None, **settings):
    db = Database.for_dir(tmp_path / "state")
    db.put_doc("marker", {"v": 1})
    cfg = SimpleNamespace(settings=SimpleNamespace(state_backup=StateBackupSettings(**settings)))
    store = SimpleNamespace(get=lambda: cfg)
    loop = bk.StateBackupLoop(store, db, tmp_path / "state", today=clock or Clock(), startup_delay=0)
    return db, loop, tmp_path / "state" / "backups", cfg


def names(folder):
    return sorted(p.name for p in folder.iterdir())


def test_defaults_work_with_no_config():
    s = StateBackupSettings()
    assert s.enabled is True and s.folder == "" and s.retention_days == 30 and s.retry_minutes == 30


@pytest.mark.parametrize("bad", [{"retention_days": 0}, {"retention_days": -1}, {"retry_minutes": 0}, {"enabled": "maybe"}])
def test_schema_rejects_bad_values(bad):
    with pytest.raises(Exception):
        StateBackupSettings(**bad)


def test_first_copy_has_the_exact_name_and_the_right_data(tmp_path):
    db, loop, folder, _ = make(tmp_path)
    created = loop.run_once()
    assert created == folder / "relay_state_2026-11-02.db"
    assert names(folder) == ["relay_state_2026-11-02.db"]
    copy = sqlite3.connect(str(created))
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert copy.execute("SELECT body FROM docs WHERE name='marker'").fetchone()[0] == '{"v": 1}'
    copy.close()


def test_restored_copy_opens_as_a_state_database_with_the_data(tmp_path):
    db, loop, folder, _ = make(tmp_path)
    created = loop.run_once()
    restore = tmp_path / "restore"
    restore.mkdir()
    (restore / "relay_state.db").write_bytes(created.read_bytes())
    restored = Database.for_dir(restore)
    assert restored.get_doc("marker") == {"v": 1}
    assert restored.integrity_check() == "ok"


def test_a_second_pass_the_same_day_and_a_restart_add_no_copy(tmp_path):
    db, loop, folder, cfg = make(tmp_path)
    loop.run_once()
    assert loop.run_once() is None
    restarted = bk.StateBackupLoop(SimpleNamespace(get=lambda: cfg), db, tmp_path / "state", today=Clock(), startup_delay=0)
    assert restarted.run_once() is None
    assert names(folder) == ["relay_state_2026-11-02.db"]


def test_the_next_utc_day_takes_one_more_copy(tmp_path):
    clock = Clock()
    db, loop, folder, _ = make(tmp_path, clock)
    loop.run_once()
    clock.day = DAY + timedelta(days=1)
    loop.run_once()
    loop.run_once()
    assert names(folder) == ["relay_state_2026-11-02.db", "relay_state_2026-11-03.db"]


def test_catch_up_after_downtime_takes_one_copy_not_one_per_missed_day(tmp_path):
    clock = Clock()
    db, loop, folder, _ = make(tmp_path, clock)
    loop.run_once()
    clock.day = DAY + timedelta(days=9)
    loop.run_once()
    assert names(folder) == ["relay_state_2026-11-02.db", "relay_state_2026-11-11.db"]


def seed(folder, days_old, today=DAY):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / bk.backup_name(today - timedelta(days=days_old))
    path.write_bytes(b"old")
    return path


def test_retention_boundary_keeps_30_days_and_deletes_31(tmp_path):
    db, loop, folder, _ = make(tmp_path)
    keep = [seed(folder, 0 + n) for n in (1, 29, 30)]
    drop = [seed(folder, n) for n in (31, 45, 400)]
    loop.run_once()
    assert all(p.exists() for p in keep)
    assert not any(p.exists() for p in drop)


def test_retention_follows_the_configured_days_and_hot_reloads(tmp_path):
    db, loop, folder, cfg = make(tmp_path, retention_days=7)
    a, b = seed(folder, 7), seed(folder, 8)
    loop.run_once()
    assert a.exists() and not b.exists()
    cfg.settings.state_backup = StateBackupSettings(retention_days=3)
    loop._today = Clock(DAY + timedelta(days=1))
    loop.run_once()
    assert not a.exists()


def test_unrelated_files_in_the_folder_are_never_touched(tmp_path):
    db, loop, folder, _ = make(tmp_path)
    folder.mkdir(parents=True)
    keepers = [
        "notes.txt", "relay_state_2020-01-01.db.bak", "relay_state_2020-01-01.DB", "relay_state_2020-1-1.db",
        "my_relay_state_2020-01-01.db", "relay_state_2020-01-01.db.partial-x", "relay_state_9999-99-99.db",
        "relay_state_2020-01-01.db.partial-12-ZZZZZZZZ",
    ]
    for name in keepers:
        (folder / name).write_text("mine")
    (folder / "sub").mkdir()
    (folder / "sub" / "relay_state_2020-01-01.db").write_text("mine")
    old_time = time.time() - 10 * 86400
    for name in keepers:
        os.utime(folder / name, (old_time, old_time))
    loop.run_once()
    for name in keepers:
        assert (folder / name).read_text() == "mine", name
    assert (folder / "sub" / "relay_state_2020-01-01.db").exists()


def test_the_newest_copy_is_kept_even_when_it_is_older_than_retention(tmp_path):
    folder = tmp_path / "b"
    old = [seed(folder, n) for n in (100, 200, 300)]
    removed = bk.prune(folder, DAY, 30)
    assert sorted(removed) == sorted(old[1:])
    assert old[0].exists()


def test_a_single_old_copy_is_never_deleted(tmp_path):
    folder = tmp_path / "b"
    only = seed(folder, 500)
    assert bk.prune(folder, DAY, 30) == [] and only.exists()


def test_nothing_is_pruned_when_the_new_copy_fails(tmp_path, monkeypatch, caplog):
    db, loop, folder, _ = make(tmp_path)
    old = seed(folder, 90)
    monkeypatch.setattr(bk, "_verify", lambda p: (_ for _ in ()).throw(RuntimeError("integrity check failed: boom")))
    assert loop.run_once() is None
    assert old.exists()
    assert names(folder) == [old.name]
    assert "state database backup failed" in caplog.text
    status = bk.read_status(db)
    assert "boom" in status[bk.META_ERROR] and bk.META_OK_AT not in status


def test_failed_copy_leaves_no_temp_and_retries_successfully_later(tmp_path, monkeypatch):
    db, loop, folder, _ = make(tmp_path)
    real = bk._copy_in_steps

    def broken(db_path, dest):
        dest.write_bytes(b"half")
        raise OSError("disk full")

    monkeypatch.setattr(bk, "_copy_in_steps", broken)
    assert loop.run_once() is None
    assert names(folder) == []
    monkeypatch.setattr(bk, "_copy_in_steps", real)
    assert loop.run_once() is not None
    status = bk.read_status(db)
    assert bk.META_ERROR not in status and status[bk.META_OK_AT].endswith("Z")


def test_a_corrupt_copy_is_rejected_and_never_published(tmp_path, monkeypatch):
    db, loop, folder, _ = make(tmp_path)
    monkeypatch.setattr(bk, "_copy_in_steps", lambda src, dest: dest.write_bytes(b"not a database" * 100))
    assert loop.run_once() is None
    assert names(folder) == []


def test_crash_leftovers_are_cleaned_only_when_stale_and_matching(tmp_path):
    db, loop, folder, _ = make(tmp_path)
    folder.mkdir(parents=True)
    stale = folder / "relay_state_2026-11-01.db.partial-123-abcdef01"
    fresh = folder / "relay_state_2026-11-01.db.partial-456-abcdef02"
    other = folder / "relay_state_2026-11-01.db.partial-other"
    for p in (stale, fresh, other):
        p.write_bytes(b"x")
    old = time.time() - 2 * 3600
    os.utime(stale, (old, old))
    os.utime(other, (old, old))
    loop.run_once()
    assert not stale.exists() and fresh.exists() and other.exists()


def test_two_relays_on_one_folder_make_one_valid_copy_and_never_replace_it(tmp_path):
    db, loop, folder, cfg = make(tmp_path)
    first = bk.create_copy(db, folder, DAY)
    marker = first.read_bytes()
    assert bk.create_copy(db, folder, DAY) is None
    assert first.read_bytes() == marker
    assert names(folder) == [first.name]
    # simulate the loser of a race: its finished temp must be discarded, not replace the winner
    temp = folder / "relay_state_2026-11-02.db.partial-1-deadbeef"
    temp.write_bytes(b"loser")
    assert bk._publish(temp, first) is False
    assert not temp.exists() and first.read_bytes() == marker


def test_publish_falls_back_to_rename_when_hard_links_are_unsupported(tmp_path, monkeypatch):
    temp, final = tmp_path / "t", tmp_path / "relay_state_2026-11-02.db"
    temp.write_bytes(b"data")
    monkeypatch.setattr(os, "link", lambda *a: (_ for _ in ()).throw(OSError("not supported")))
    assert bk._publish(temp, final) is True
    assert final.read_bytes() == b"data" and not temp.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_copy_and_folder_are_private_on_posix(tmp_path):
    db, loop, folder, _ = make(tmp_path)
    created = loop.run_once()
    assert stat.S_IMODE(created.stat().st_mode) == 0o600
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_disabled_setting_does_nothing(tmp_path):
    db, loop, folder, _ = make(tmp_path, enabled=False)
    assert loop.run_once() is None
    assert not folder.exists()


def test_custom_folder_setting(tmp_path):
    target = tmp_path / "elsewhere"
    db, loop, folder, _ = make(tmp_path, folder=str(target))
    loop.run_once()
    assert names(target) == ["relay_state_2026-11-02.db"] and not folder.exists()


def test_copy_does_not_block_writers_for_long(tmp_path):
    db, loop, folder, _ = make(tmp_path)
    for n in range(300):
        db.put_doc(f"bulk{n}", {"pad": "x" * 2000}, durable=False)
    db.flush()
    import threading

    t = threading.Thread(target=loop.run_once)
    t.start()
    worst = 0.0
    while t.is_alive():
        started = time.monotonic()
        db.put_doc("ping", {"t": started})
        worst = max(worst, time.monotonic() - started)
    t.join()
    assert worst < 2.0
    assert (folder / "relay_state_2026-11-02.db").exists()


async def test_the_loop_runs_in_the_background_and_stops(tmp_path):
    db, loop, folder, _ = make(tmp_path)
    task = asyncio.create_task(loop.run_forever())
    for _ in range(600):
        if folder.exists() and list(folder.glob("relay_state_*.db")):
            break
        await asyncio.sleep(0.05)
    loop.stop()
    task.cancel()
    assert names(folder) == ["relay_state_2026-11-02.db"]


def test_a_state_database_folder_copy_is_git_ignored():
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    if not (repo / ".git").exists() or shutil.which("git") is None:
        pytest.skip("not a git checkout (source archive) or git is not installed")
    result = subprocess.run(
        ["git", "check-ignore", "relay/state/backups/relay_state_2026-11-02.db"], cwd=repo, capture_output=True, text=True
    )
    assert result.returncode == 0, "backup copies must be ignored by .gitignore"
