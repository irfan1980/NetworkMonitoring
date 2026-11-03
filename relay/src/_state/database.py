from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

logger = logging.getLogger("relay.state.db")

DB_FILENAME = "relay_state.db"
BUSY_TIMEOUT_MS = 5000
WRITE_RETRIES = 4
WRITER_IDLE_SECONDS = 1.0
IMPORT_MARKER = "files_imported_at"
ACCOUNTS_MARKER = "accounts_imported_at"

# Each entry is one schema version; migration N runs in its own transaction and bumps PRAGMA user_version.
MIGRATIONS: list[list[str]] = [
    [
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "CREATE TABLE docs (name TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at REAL NOT NULL)",
        "CREATE TABLE schedules (id INTEGER PRIMARY KEY, body TEXT NOT NULL)",
        """CREATE TABLE sessions (
            hash TEXT PRIMARY KEY, account TEXT NOT NULL, account_key TEXT NOT NULL, device_name TEXT NOT NULL,
            platform TEXT NOT NULL, created_at REAL NOT NULL, last_used_at REAL NOT NULL, expires_at REAL NOT NULL,
            push_token TEXT, device_id TEXT)""",
        """CREATE TABLE schedule_runs (
            schedule_id INTEGER NOT NULL, seq INTEGER NOT NULL, body TEXT NOT NULL,
            PRIMARY KEY (schedule_id, seq))""",
    ],
    [
        # account_secret, name, min_severity in this order and nothing else, so `INSERT INTO user_accounts VALUES (...)` takes
        # exactly three values. Triggers (not foreign keys) cascade, because a sqlite3 CLI session has foreign_keys off.
        """CREATE TABLE user_accounts (
            account_secret TEXT NOT NULL PRIMARY KEY CHECK (length(account_secret) > 0),
            name TEXT NOT NULL CHECK (length(name) > 0),
            min_severity INTEGER NOT NULL DEFAULT 5 CHECK (min_severity BETWEEN 1 AND 5))""",
        """CREATE TABLE account_devices (
            token TEXT NOT NULL PRIMARY KEY, account_secret TEXT NOT NULL, platform TEXT NOT NULL,
            device_name TEXT NOT NULL DEFAULT '', registered_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, device_id TEXT)""",
        "CREATE INDEX account_devices_by_account ON account_devices(account_secret)",
        "CREATE TABLE account_push_windows (account_secret TEXT NOT NULL PRIMARY KEY, window TEXT NOT NULL)",
        """CREATE TRIGGER user_accounts_after_delete AFTER DELETE ON user_accounts BEGIN
            DELETE FROM account_devices WHERE account_secret = OLD.account_secret;
            DELETE FROM account_push_windows WHERE account_secret = OLD.account_secret;
        END""",
        """CREATE TRIGGER user_accounts_after_secret_update AFTER UPDATE OF account_secret ON user_accounts BEGIN
            UPDATE account_devices SET account_secret = NEW.account_secret WHERE account_secret = OLD.account_secret;
            UPDATE account_push_windows SET account_secret = NEW.account_secret WHERE account_secret = OLD.account_secret;
        END""",
    ],
]
SCHEMA_VERSION = len(MIGRATIONS)


class StateDbError(Exception):
    pass


_registry: "weakref.WeakValueDictionary[str, Database]" = weakref.WeakValueDictionary()
_registry_lock = threading.Lock()


class Database:
    """One SQLite file in the state dir, rollback journal (never WAL: it is unsafe on network filesystems).

    One shared connection guarded by a lock. Durable writes commit before returning; `submit` queues a coalesced
    write for a background writer thread so hot, low-value updates do not block the caller on fsync."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._cv = threading.Condition()
        self._pending: dict[Any, Callable[[sqlite3.Connection], None]] = {}
        self._busy = False
        self._thread: Optional[threading.Thread] = None
        self._conn = sqlite3.connect(
            str(path), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None, check_same_thread=False
        )
        try:
            self._configure()
            self._migrate()
            self._restrict_file()
        except BaseException:
            self._conn.close()
            raise

    @classmethod
    def for_dir(cls, state_dir: Path | str, accounts_file: Path | str | None = None) -> "Database":
        base = Path(state_dir).resolve()
        key = str(base)
        with _registry_lock:
            existing = _registry.get(key)
            if existing is not None:
                return existing
            base.mkdir(parents=True, exist_ok=True)
            db = cls(base / DB_FILENAME)
            try:
                db._import_once(base, Path(accounts_file) if accounts_file else None)
            except BaseException:
                db._conn.close()
                raise
            _registry[key] = db
            return db

    def _restrict_file(self) -> None:
        # the file holds account secrets and session hashes; the rollback journal is created with the same mode
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _configure(self) -> None:
        mode = self._conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if str(mode).lower() != "delete":
            raise StateDbError(f"{self.path}: could not select the rollback journal (got journal_mode={mode})")
        # FULL is the safe default; the override exists so the test suite does not pay an fsync per commit
        sync = os.environ.get("RELAY_STATE_SYNCHRONOUS", "FULL").upper()
        if sync not in ("FULL", "NORMAL", "OFF"):
            raise StateDbError(f"RELAY_STATE_SYNCHRONOUS must be FULL, NORMAL or OFF, got {sync!r}")
        self._conn.execute(f"PRAGMA synchronous={sync}")
        self._conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise StateDbError(
                f"{self.path} has schema version {version}, newer than this relay supports ({SCHEMA_VERSION}); "
                "upgrade the relay or restore an older database"
            )
        for index in range(version, SCHEMA_VERSION):
            with self.transaction() as conn:
                for statement in MIGRATIONS[index]:
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version={index + 1}")

    def _import_once(self, state_dir: Path, accounts_file: Optional[Path] = None) -> None:
        from _state.importer import import_accounts_file, import_state_files

        counts: dict = {}
        stamp = str(time.time())
        with self.transaction() as conn:
            files_done = conn.execute("SELECT 1 FROM meta WHERE key=?", (IMPORT_MARKER,)).fetchone()
            accounts_done = conn.execute("SELECT 1 FROM meta WHERE key=?", (ACCOUNTS_MARKER,)).fetchone()
            if not files_done:
                counts = import_state_files(conn, state_dir, accounts_file)
                conn.execute("INSERT INTO meta(key, value) VALUES (?, ?)", (IMPORT_MARKER, stamp))
            elif not accounts_done:
                # a database created before accounts moved in: its files marker is set, so accounts get their own one-time import
                counts = import_accounts_file(conn, accounts_file or state_dir / "auth" / "push_accounts.json")
            if not accounts_done:
                conn.execute("INSERT INTO meta(key, value) VALUES (?, ?)", (ACCOUNTS_MARKER, stamp))
            elif files_done:
                return
        if counts:
            logger.info("imported existing state files into %s: %s", self.path.name, counts)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def read(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        with self._lock:
            return fn(self._conn)

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def write(self, fn: Callable[[sqlite3.Connection], None]) -> None:
        """Run fn in one transaction and return only after it is committed."""
        self.flush()
        self._run(fn)

    def _run(self, fn: Callable[[sqlite3.Connection], None]) -> None:
        for attempt in range(1, WRITE_RETRIES + 1):
            try:
                with self.transaction() as conn:
                    fn(conn)
                return
            except sqlite3.OperationalError as exc:
                text = str(exc).lower()
                transient = "locked" in text or "busy" in text or "disk i/o" in text
                if not transient or attempt == WRITE_RETRIES:
                    raise
                time.sleep(0.05 * attempt)

    def submit(self, key: Any, fn: Callable[[sqlite3.Connection], None]) -> None:
        """Queue a write for the background writer; a later submit with the same key replaces an unwritten one.
        fn must close over already-snapshotted data, not live objects."""
        with self._cv:
            self._pending[key] = fn
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._writer, name="relay-state-writer", daemon=True)
                self._thread.start()
            self._cv.notify_all()

    def _writer(self) -> None:
        while True:
            with self._cv:
                if not self._pending:
                    self._cv.wait(WRITER_IDLE_SECONDS)
                if not self._pending:
                    self._thread = None
                    self._cv.notify_all()
                    return
                items = list(self._pending.values())
                self._pending.clear()
                self._busy = True
            try:
                def apply(conn: sqlite3.Connection) -> None:
                    for fn in items:
                        fn(conn)

                self._run(apply)
            except Exception:
                logger.exception("background state write failed; the in-memory state is kept and the next change retries")
            finally:
                with self._cv:
                    self._busy = False
                    self._cv.notify_all()

    def flush(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        with self._cv:
            while self._pending or self._busy:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning("state writer did not drain within %ss", timeout)
                    return
                self._cv.wait(remaining)

    def get_doc(self, name: str) -> Optional[Any]:
        rows = self.query("SELECT body FROM docs WHERE name=?", (name,))
        return json.loads(rows[0][0]) if rows else None

    def put_doc(self, name: str, data: Any, *, durable: bool = True) -> None:
        body = json.dumps(data)
        stamp = time.time()

        def apply(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO docs(name, body, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET body=excluded.body, updated_at=excluded.updated_at",
                (name, body, stamp),
            )

        if durable:
            self.write(apply)
        else:
            self.submit(("doc", name), apply)

    def backup_to(self, dest: Path | str) -> None:
        """Consistent online copy via the SQLite backup API; safe while the relay is running."""
        self.flush()
        target = sqlite3.connect(str(dest))
        try:
            with self._lock:
                self._conn.backup(target)
        finally:
            target.close()

    def integrity_check(self) -> str:
        return str(self.query("PRAGMA integrity_check")[0][0])
