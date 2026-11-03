from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from _state import database as dbmod
from _state.database import DB_FILENAME, SCHEMA_VERSION, Database, StateDbError


def fresh(tmp_path) -> Database:
    return Database.for_dir(tmp_path)


def reopen(tmp_path) -> Database:
    dbmod._registry.clear()
    return Database.for_dir(tmp_path)


def test_one_file_in_the_state_dir_with_a_rollback_journal_never_wal(tmp_path, monkeypatch):
    monkeypatch.delenv("RELAY_STATE_SYNCHRONOUS")
    db = fresh(tmp_path)
    assert (tmp_path / DB_FILENAME).is_file()
    assert db.query("PRAGMA journal_mode")[0][0] == "delete"
    assert db.query("PRAGMA synchronous")[0][0] == 2
    assert db.query("PRAGMA busy_timeout")[0][0] == dbmod.BUSY_TIMEOUT_MS
    db.put_doc("x", {"a": 1})
    assert not list(tmp_path.glob("*-wal")) and not list(tmp_path.glob("*-shm"))


def test_schema_version_is_set_and_reopen_is_a_noop(tmp_path):
    db = fresh(tmp_path)
    assert db.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION
    db.put_doc("keep", {"v": 1})
    assert reopen(tmp_path).get_doc("keep") == {"v": 1}


def test_a_database_from_a_newer_relay_is_refused(tmp_path):
    fresh(tmp_path)
    dbmod._registry.clear()
    raw = sqlite3.connect(str(tmp_path / DB_FILENAME))
    raw.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    raw.close()
    with pytest.raises(StateDbError, match="newer"):
        Database.for_dir(tmp_path)


def test_migrations_apply_in_order_from_an_older_version(tmp_path, monkeypatch):
    path = tmp_path / DB_FILENAME
    old = sqlite3.connect(str(path))
    for migration in dbmod.MIGRATIONS:
        for statement in migration:
            old.execute(statement)
    old.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    old.execute("INSERT INTO meta(key, value) VALUES ('files_imported_at', '1')")
    old.execute("INSERT INTO meta(key, value) VALUES ('accounts_imported_at', '1')")
    old.commit()
    old.close()
    monkeypatch.setattr(dbmod, "MIGRATIONS", [*dbmod.MIGRATIONS, ["CREATE TABLE extra (id INTEGER)"]])
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", SCHEMA_VERSION + 1)
    dbmod._registry.clear()
    db = Database.for_dir(tmp_path)
    assert db.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION + 1
    assert db.query("SELECT name FROM sqlite_master WHERE name='extra'")


def test_a_failed_migration_rolls_back_and_leaves_the_version(tmp_path, monkeypatch):
    fresh(tmp_path)
    dbmod._registry.clear()
    monkeypatch.setattr(dbmod, "MIGRATIONS", [*dbmod.MIGRATIONS, ["CREATE TABLE ok_table (id INTEGER)", "THIS IS NOT SQL"]])
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", SCHEMA_VERSION + 1)
    with pytest.raises(sqlite3.Error):
        Database.for_dir(tmp_path)
    check = sqlite3.connect(str(tmp_path / DB_FILENAME))
    assert check.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert not check.execute("SELECT 1 FROM sqlite_master WHERE name='ok_table'").fetchall()
    check.close()


def test_a_failed_transaction_rolls_back_every_statement(tmp_path):
    db = fresh(tmp_path)

    def boom(conn):
        conn.execute("INSERT INTO docs(name, body, updated_at) VALUES ('a', '{}', 0)")
        raise RuntimeError("mid-write failure")

    with pytest.raises(RuntimeError):
        db.write(boom)
    assert db.get_doc("a") is None


def test_a_file_that_is_not_a_database_is_reported(tmp_path):
    (tmp_path / DB_FILENAME).write_bytes(b"this is not sqlite" * 50)
    with pytest.raises(sqlite3.DatabaseError):
        Database.for_dir(tmp_path)


def test_submit_coalesces_by_key_and_flush_waits_for_the_commit(tmp_path):
    db = fresh(tmp_path)
    for n in range(50):
        db.put_doc("hot", {"n": n}, durable=False)
    db.flush()
    assert db.get_doc("hot") == {"n": 49}
    assert reopen(tmp_path).get_doc("hot") == {"n": 49}


def test_submit_does_not_block_the_caller_while_the_database_is_busy(tmp_path):
    db = fresh(tmp_path)
    held, release = threading.Event(), threading.Event()

    def hog():
        with db.transaction():
            held.set()
            release.wait(5)

    t = threading.Thread(target=hog)
    t.start()
    held.wait(2)
    started = time.monotonic()
    db.put_doc("later", {"ok": True}, durable=False)
    assert time.monotonic() - started < 0.25
    release.set()
    t.join()
    db.flush()
    assert db.get_doc("later") == {"ok": True}


def test_a_failing_background_write_is_logged_and_the_writer_survives(tmp_path, caplog):
    db = fresh(tmp_path)
    db.submit("bad", lambda conn: conn.execute("INSERT INTO no_such_table VALUES (1)"))
    db.flush()
    assert "background state write failed" in caplog.text
    db.put_doc("after", {"ok": 1}, durable=False)
    db.flush()
    assert db.get_doc("after") == {"ok": 1}


def test_durable_write_waits_for_earlier_queued_writes_so_order_is_kept(tmp_path):
    db = fresh(tmp_path)
    for n in range(20):
        db.put_doc("k", {"n": n}, durable=False)
        db.put_doc("k", {"n": 100 + n})
    assert db.get_doc("k") == {"n": 119}


def test_online_backup_is_a_consistent_openable_copy(tmp_path):
    db = fresh(tmp_path)
    db.put_doc("a", {"v": 1})
    dest = tmp_path / "copy.db"
    db.backup_to(dest)
    copy = sqlite3.connect(str(dest))
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert copy.execute("SELECT body FROM docs WHERE name='a'").fetchone()[0] == '{"v": 1}'
    copy.close()


def test_concurrent_threads_never_corrupt_or_lose_writes(tmp_path):
    db = fresh(tmp_path)
    errors: list[BaseException] = []

    def worker(i):
        try:
            for n in range(40):
                db.put_doc(f"w{i}", {"n": n})
                db.put_doc(f"hot{i}", {"n": n}, durable=False)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    db.flush()
    assert errors == []
    assert db.integrity_check() == "ok"
    for i in range(8):
        assert db.get_doc(f"w{i}") == {"n": 39} and db.get_doc(f"hot{i}") == {"n": 39}


def test_two_connections_to_one_file_serialise_through_the_busy_timeout(tmp_path):
    first = fresh(tmp_path)
    second = Database(tmp_path / DB_FILENAME)
    errors: list[BaseException] = []

    def writer(db, tag):
        try:
            for n in range(30):
                db.put_doc(f"{tag}{n}", {"n": n})
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(first, "a")), threading.Thread(target=writer, args=(second, "b"))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert len(first.query("SELECT name FROM docs")) == 60
    assert first.integrity_check() == "ok"


def test_synchronous_override_is_validated(tmp_path, monkeypatch):
    monkeypatch.setenv("RELAY_STATE_SYNCHRONOUS", "banana")
    with pytest.raises(StateDbError, match="RELAY_STATE_SYNCHRONOUS"):
        Database.for_dir(tmp_path)
