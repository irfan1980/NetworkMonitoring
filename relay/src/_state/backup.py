from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import sqlite3
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from _push.paths import resolve_push_path
from _state.database import BUSY_TIMEOUT_MS, Database

logger = logging.getLogger("relay.state.backup")

PREFIX = "relay_state_"
FINAL_RE = re.compile(r"^relay_state_(\d{4})-(\d{2})-(\d{2})\.db$")
TEMP_RE = re.compile(r"^relay_state_\d{4}-\d{2}-\d{2}\.db\.partial-\d+-[0-9a-f]{8}$")
STALE_TEMP_SECONDS = 3600
STARTUP_DELAY_SECONDS = 60.0
COPY_PAGES = 128
COPY_SLEEP_SECONDS = 0.02

META_OK_AT = "backup_last_ok_at"
META_FILE = "backup_last_file"
META_ERROR = "backup_last_error"
META_ERROR_AT = "backup_last_error_at"


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def backup_name(day: date) -> str:
    return f"{PREFIX}{day.isoformat()}.db"


def resolve_backup_dir(folder: str, state_dir: Path | str) -> Path:
    if not folder:
        return (Path(state_dir) / "backups").resolve()
    return resolve_push_path(folder)


def _file_day(name: str) -> Optional[date]:
    m = FINAL_RE.match(name)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def list_copies(folder: Path) -> list[tuple[date, Path]]:
    """Only regular files with the job's exact name pattern; never anything else in the folder."""
    found = []
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []
    for path in entries:
        day = _file_day(path.name)
        if day is not None and path.is_file() and not path.is_symlink():
            found.append((day, path))
    return sorted(found)


def _restrict(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _verify(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA query_only=1")
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"integrity check failed: {result}")
        if conn.execute("PRAGMA user_version").fetchone()[0] < 1:
            raise RuntimeError("copy has no schema version")
    finally:
        conn.close()


def _copy_in_steps(db_path: Path, dest: Path) -> None:
    """Online copy with a private read connection, a few pages at a time, so writers wait for one short step at most."""
    source = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_MS / 1000)
    target = sqlite3.connect(str(dest))
    try:
        source.backup(target, pages=COPY_PAGES, sleep=COPY_SLEEP_SECONDS)
        target.commit()
    finally:
        target.close()
        source.close()


def _publish(temp: Path, final: Path) -> bool:
    """Make the finished copy visible without ever replacing an existing one. True when this call created it."""
    if final.exists():
        temp.unlink(missing_ok=True)
        return False
    try:
        os.link(temp, final)
    except FileExistsError:
        temp.unlink(missing_ok=True)
        return False
    except OSError:
        # hard links unsupported (some network filesystems): plain rename, still never visible half-written
        if final.exists():
            temp.unlink(missing_ok=True)
            return False
        os.rename(temp, final)
        return True
    temp.unlink(missing_ok=True)
    return True


def create_copy(db: Database, folder: Path, day: date) -> Optional[Path]:
    """Returns the new copy, or None when today's copy already exists. Raises on failure after removing its temp file."""
    folder.mkdir(parents=True, exist_ok=True)
    _restrict(folder, 0o700)
    final = folder / backup_name(day)
    if final.exists():
        return None
    temp = folder / f"{final.name}.partial-{os.getpid()}-{secrets.token_hex(4)}"
    try:
        db.flush()
        _copy_in_steps(db.path, temp)
        _verify(temp)
        _restrict(temp, 0o600)
        if not _publish(temp, final):
            return None
        _restrict(final, 0o600)
        return final
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def clean_stale_temps(folder: Path, now: Optional[float] = None) -> int:
    now = time.time() if now is None else now
    removed = 0
    try:
        entries = list(folder.iterdir())
    except OSError:
        return 0
    for path in entries:
        if TEMP_RE.match(path.name) and path.is_file() and not path.is_symlink():
            try:
                if now - path.stat().st_mtime >= STALE_TEMP_SECONDS:
                    path.unlink()
                    removed += 1
            except OSError:
                pass
    return removed


def prune(folder: Path, today: date, retention_days: int) -> list[Path]:
    """Delete copies dated more than retention_days ago; the newest copy always stays, and future-dated files are left alone."""
    copies = list_copies(folder)
    if not copies:
        return []
    newest = copies[-1][1]
    removed = []
    for day, path in copies:
        if path == newest or day > today:
            continue
        if (today - day).days > retention_days:
            try:
                path.unlink()
                removed.append(path)
            except OSError as exc:
                logger.warning("could not delete old state backup %s: %s", path.name, exc)
    return removed


def _set_meta(db: Database, values: dict[str, Optional[str]]) -> None:
    def apply(conn: sqlite3.Connection) -> None:
        for key, value in values.items():
            if value is None:
                conn.execute("DELETE FROM meta WHERE key=?", (key,))
            else:
                conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    db.write(apply)


def read_status(db: Database) -> dict[str, str]:
    keys = (META_OK_AT, META_FILE, META_ERROR, META_ERROR_AT)
    rows = db.query(f"SELECT key, value FROM meta WHERE key IN ({','.join('?' for _ in keys)})", keys)
    return {k: v for k, v in rows}


class StateBackupLoop:
    """Daily (UTC date) online copy of the state database. One copy per UTC date, decided by the file's existence, so
    restarts and a second relay on the same folder never add copies."""

    def __init__(
        self,
        config_store,
        db: Database,
        state_dir: Path | str,
        today: Callable[[], date] = utc_today,
        startup_delay: float = STARTUP_DELAY_SECONDS,
    ):
        self._config_store = config_store
        self._db = db
        self._state_dir = Path(state_dir)
        self._today = today
        self._startup_delay = startup_delay
        self._running = False

    def _settings(self):
        return self._config_store.get().settings.state_backup

    def run_once(self) -> Optional[Path]:
        """One blocking pass; run it in a thread. Never raises."""
        settings = self._settings()
        if not settings.enabled:
            return None
        folder = resolve_backup_dir(settings.folder, self._state_dir)
        today = self._today()
        try:
            clean_stale_temps(folder)
            created = create_copy(self._db, folder, today)
            if created is None:
                return None
            removed = prune(folder, today, settings.retention_days)
            _set_meta(self._db, {
                META_OK_AT: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                META_FILE: str(created), META_ERROR: None, META_ERROR_AT: None,
            })
            logger.info("state database copied to %s; removed %d copy(ies) older than %d days", created, len(removed), settings.retention_days)
            return created
        except Exception as exc:
            logger.error("state database backup failed (will retry in %s min): %s", settings.retry_minutes, exc)
            try:
                _set_meta(self._db, {
                    META_ERROR: str(exc)[:500],
                    META_ERROR_AT: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
            except Exception:
                logger.exception("could not record the backup failure")
            return None

    async def run_forever(self) -> None:
        self._running = True
        await asyncio.sleep(self._startup_delay)
        while self._running:
            await asyncio.get_running_loop().run_in_executor(None, self.run_once)
            if not self._running:
                return
            await asyncio.sleep(max(1.0, self._settings().retry_minutes * 60))

    def stop(self) -> None:
        self._running = False
