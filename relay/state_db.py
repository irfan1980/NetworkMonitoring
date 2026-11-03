"""Back up, check and export the relay's SQLite state database.

    python state_db.py backup  <dest-file>   consistent copy; safe while the relay is running
    python state_db.py check                 integrity check, row counts, last daily copy and its error, if any
    python state_db.py export  <dest-dir>    write the old state-file layout (rollback to a pre-database relay); the exported
                                             auth/push_accounts.json holds the PLAIN account secrets (mode 0600 where supported)

The state dir is RELAY_STATE_DIR or relay/state. To restore, stop the relay, replace relay_state.db with the backup
(delete a leftover relay_state.db-journal), and start it.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from _state.database import DB_FILENAME, Database  # noqa: E402
from _state.backup import META_ERROR, META_ERROR_AT, META_FILE, META_OK_AT, list_copies, read_status  # noqa: E402
from _state.importer import export_state_files  # noqa: E402


def _state_dir() -> Path:
    return Path(os.environ.get("RELAY_STATE_DIR") or ROOT / "state")


def _open_existing() -> Database:
    path = _state_dir() / DB_FILENAME
    if not path.is_file():
        raise SystemExit(f"no state database at {path}")
    return Database.for_dir(_state_dir())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backup").add_argument("dest")
    sub.add_parser("check")
    sub.add_parser("export").add_argument("dest")
    args = parser.parse_args(argv)

    db = _open_existing()
    if args.command == "backup":
        dest = Path(args.dest)
        if dest.exists():
            raise SystemExit(f"{dest} already exists; refusing to overwrite")
        db.backup_to(dest)
        check = sqlite3.connect(str(dest))
        try:
            result = check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            check.close()
        print(f"backup written to {dest} (integrity: {result})")
        return 0 if result == "ok" else 1
    if args.command == "check":
        print("integrity:", db.integrity_check())
        for table in ("user_accounts", "account_devices", "schedules", "schedule_runs", "sessions", "docs"):
            print(f"{table}: {db.query(f'SELECT COUNT(*) FROM {table}')[0][0]}")
        status = read_status(db)
        print("last daily copy:", status.get(META_OK_AT, "never"), status.get(META_FILE, ""))
        if META_ERROR in status:
            print("last copy error:", status[META_ERROR_AT], status[META_ERROR])
        for day, path in list_copies(_state_dir() / "backups"):
            print(f"  copy {path.name}")
        return 0
    dest = Path(args.dest)
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"{dest} is not empty; refusing to export into it")
    with db.transaction() as conn:
        counts = export_state_files(conn, dest)
    print(f"exported to {dest}: {counts}")
    print("warning: auth/push_accounts.json in the export contains account secrets; keep it private")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
