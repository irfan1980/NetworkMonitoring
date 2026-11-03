from __future__ import annotations

import sqlite3

import pytest

import state_db
from _state import database as dbmod
from _state.database import Database


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("RELAY_STATE_DIR", str(tmp_path))
    Database.for_dir(tmp_path).put_doc("push/mutes", {"a": ["k"]})
    return tmp_path


def test_backup_is_a_consistent_copy_and_never_overwrites(state, tmp_path, capsys):
    dest = tmp_path / "out" / "b.db"
    dest.parent.mkdir()
    assert state_db.main(["backup", str(dest)]) == 0
    assert "integrity: ok" in capsys.readouterr().out
    assert sqlite3.connect(str(dest)).execute("SELECT COUNT(*) FROM docs").fetchone()[0] == 1
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        state_db.main(["backup", str(dest)])


def test_check_reports_the_last_daily_copy_and_its_error(state, capsys):
    from _state import backup

    db = Database.for_dir(state)
    assert state_db.main(["check"]) == 0
    assert "last daily copy: never" in capsys.readouterr().out
    backup._set_meta(db, {backup.META_OK_AT: "2026-11-02T01:00:00Z", backup.META_FILE: "x.db",
                          backup.META_ERROR: "disk full", backup.META_ERROR_AT: "2026-11-03T01:00:00Z"})
    state_db.main(["check"])
    out = capsys.readouterr().out
    assert "2026-11-02T01:00:00Z" in out and "last copy error: 2026-11-03T01:00:00Z disk full" in out


def test_check_and_export(state, tmp_path, capsys):
    assert state_db.main(["check"]) == 0
    assert "integrity: ok" in capsys.readouterr().out
    out = tmp_path / "export"
    assert state_db.main(["export", str(out)]) == 0
    assert (out / "push" / "mutes.json").is_file()
    with pytest.raises(SystemExit, match="not empty"):
        state_db.main(["export", str(out)])


def test_missing_database_is_reported_without_creating_one(tmp_path, monkeypatch):
    empty = tmp_path / "nothing"
    empty.mkdir()
    monkeypatch.setenv("RELAY_STATE_DIR", str(empty))
    dbmod._registry.clear()
    with pytest.raises(SystemExit, match="no state database"):
        state_db.main(["check"])
    assert not (empty / dbmod.DB_FILENAME).exists()
