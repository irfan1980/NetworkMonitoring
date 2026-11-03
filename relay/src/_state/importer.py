from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger("relay.state.import")

DOC_INFLIGHT = "scheduler/inflight"
DOC_PUSH_STATE = "push/state"
DOC_MUTES = "push/mutes"
DOC_DOWNTIME = "push/downtime"
DOC_ACKS = "push/acks"
META_NEXT_SCHEDULE_ID = "schedules_next_id"

# doc name -> the file it came from before the SQLite state database
DOC_FILES = {
    DOC_INFLIGHT: ("scheduler", "inflight.json"),
    DOC_PUSH_STATE: ("push", "state.json"),
    DOC_MUTES: ("push", "mutes.json"),
    DOC_DOWNTIME: ("push", "downtime.json"),
    DOC_ACKS: ("push", "acks.json"),
}
SCHEDULES_FILE = ("scheduler", "schedules.json")
SESSIONS_FILE = ("auth", "sessions.json")
RUNS_DIR = ("scheduler", "runs")
ACCOUNTS_FILE = ("auth", "push_accounts.json")


class StateImportError(Exception):
    """An existing state file could not be imported; the relay must not start with empty state."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StateImportError(
            f"cannot import {path}: {exc}. Fix or move the file aside (it is left untouched) and start again."
        ) from exc


def _bad(path: Path, why: str) -> StateImportError:
    return StateImportError(f"cannot import {path}: {why}. Fix or move the file aside (it is left untouched) and start again.")


def _import_schedules(conn: sqlite3.Connection, path: Path) -> int:
    from pydantic import ValidationError

    from _scheduler.model import Schedule

    raw = _read_json(path)
    if not isinstance(raw, dict) or not isinstance(raw.get("schedules", []), list):
        raise _bad(path, "expected an object with a 'schedules' list")
    try:
        schedules = [Schedule.model_validate(item) for item in raw.get("schedules", [])]
        declared = int(raw.get("next_id", 1))
    except (ValidationError, TypeError, ValueError) as exc:
        raise _bad(path, f"invalid schedule entry ({exc})") from exc
    ids = [s.id for s in schedules]
    if len(set(ids)) != len(ids):
        raise _bad(path, "duplicate schedule ids")
    for schedule in schedules:
        conn.execute("INSERT INTO schedules(id, body) VALUES (?, ?)", (schedule.id, json.dumps(schedule.model_dump(mode="json"))))
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (META_NEXT_SCHEDULE_ID, str(max(declared, max(ids, default=0) + 1))),
    )
    return len(schedules)


def _import_sessions(conn: sqlite3.Connection, path: Path) -> int:
    from _session.store import SessionRecord

    raw = _read_json(path)
    if not isinstance(raw, dict) or not isinstance(raw.get("sessions", []), list):
        raise _bad(path, "expected an object with a 'sessions' list")
    try:
        records = [SessionRecord(**item) for item in raw.get("sessions", [])]
    except TypeError as exc:
        raise _bad(path, f"invalid session entry ({exc})") from exc
    for r in records:
        conn.execute(
            "INSERT OR REPLACE INTO sessions(hash, account, account_key, device_name, platform, created_at, last_used_at,"
            " expires_at, push_token, device_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r.hash, r.account, r.account_key, r.device_name, r.platform, r.created_at, r.last_used_at, r.expires_at,
             r.push_token, r.device_id),
        )
    return len(records)


def _validate_inflight(path: Path, raw: Any) -> None:
    from _scheduler.inflight import ActiveRun

    if not isinstance(raw, dict) or not isinstance(raw.get("runs", []), list):
        raise _bad(path, "expected an object with a 'runs' list")
    try:
        [ActiveRun(**item) for item in raw.get("runs", [])]
    except TypeError as exc:
        raise _bad(path, f"invalid in-flight run ({exc})") from exc


def _validate_push_state(path: Path, raw: Any) -> None:
    try:
        {k: (float(v["first_seen"]), dict(v.get("sent", {}))) for k, v in raw.get("alerts", {}).items()}
        {k: float(v) for k, v in raw.get("last_pushed", {}).items()}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise _bad(path, f"unexpected structure ({exc})") from exc


def _validate_mutes(path: Path, raw: Any) -> None:
    try:
        {str(k): set(map(str, v)) for k, v in raw.items()}
    except (ValueError, TypeError, AttributeError) as exc:
        raise _bad(path, f"unexpected structure ({exc})") from exc


def _validate_downtime(path: Path, raw: Any) -> None:
    from _push.downtime import parse_window
    from _push.schedule_window import WindowError

    try:
        [parse_window(w, str(w["id"])) for w in raw["windows"]]
    except (WindowError, ValueError, KeyError, TypeError) as exc:
        raise _bad(path, f"unexpected structure ({exc})") from exc


def _validate_acks(path: Path, raw: Any) -> None:
    try:
        {str(k): (float(v["acknowledged_at"]), str(v["acknowledged_by"])) for k, v in raw["acks"].items()}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise _bad(path, f"unexpected structure ({exc})") from exc


_VALIDATORS = {
    DOC_INFLIGHT: _validate_inflight,
    DOC_PUSH_STATE: _validate_push_state,
    DOC_MUTES: _validate_mutes,
    DOC_DOWNTIME: _validate_downtime,
    DOC_ACKS: _validate_acks,
}


def _import_runs(conn: sqlite3.Connection, directory: Path) -> int:
    total = 0
    for path in sorted(directory.glob("*.jsonl")):
        try:
            schedule_id = int(path.stem)
        except ValueError:
            logger.warning("ignoring run log %s: the name is not a schedule id", path)
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise _bad(path, str(exc)) from exc
        seq = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("not an object")
            except ValueError:
                logger.warning("skipping unreadable run log line in %s", path)
                continue
            seq = int(row["seq"]) if isinstance(row.get("seq"), int) else seq + 1
            row["seq"] = seq
            conn.execute(
                "INSERT OR REPLACE INTO schedule_runs(schedule_id, seq, body) VALUES (?, ?, ?)",
                (schedule_id, seq, json.dumps(row)),
            )
            total += 1
    return total


def import_accounts_file(conn: sqlite3.Connection, path: Path) -> dict[str, int]:
    """One-time import of the old operator-edited accounts file (secrets, names, severities, push tokens, push windows).
    A missing file imports nothing; an unreadable or invalid one stops the start like every other state file."""
    from _push.accounts import AccountStore, _normalise_account

    if not path.is_file():
        return {}
    raw = _read_json(path)
    if not isinstance(raw, list):
        raise _bad(path, "expected a JSON list of accounts")
    accounts = []
    secrets: set[str] = set()
    for index, entry in enumerate(raw):
        account = _normalise_account(entry)
        if account is None:
            raise _bad(path, f"account #{index + 1} has no account_secret")
        if account["account_secret"] in secrets:
            raise _bad(path, f"account #{index + 1} repeats an account_secret")
        secrets.add(account["account_secret"])
        accounts.append(account)
    for account in accounts:
        conn.execute(
            "INSERT INTO user_accounts(account_secret, name, min_severity) VALUES (?, ?, ?)",
            (account["account_secret"], account["name"], account["min_severity"]),
        )
    AccountStore.write_children(conn, accounts)
    return {"accounts": len(accounts), "devices": sum(len(a["device_codes"]) for a in accounts)}


def import_state_files(conn: sqlite3.Connection, state_dir: Path, accounts_file: Path | None = None) -> dict[str, int]:
    """Copy the pre-database state files into an empty database. Runs inside the caller's transaction, reads only,
    and raises StateImportError on any unreadable existing file so nothing is half-imported."""
    counts: dict[str, int] = {}

    path = state_dir.joinpath(*SCHEDULES_FILE)
    if path.is_file():
        counts["schedules"] = _import_schedules(conn, path)

    path = state_dir.joinpath(*SESSIONS_FILE)
    if path.is_file():
        counts["sessions"] = _import_sessions(conn, path)

    for name, parts in DOC_FILES.items():
        path = state_dir.joinpath(*parts)
        if not path.is_file():
            continue
        raw = _read_json(path)
        if not isinstance(raw, dict):
            raise _bad(path, "expected a JSON object")
        _VALIDATORS[name](path, raw)
        conn.execute("INSERT INTO docs(name, body, updated_at) VALUES (?, ?, strftime('%s','now'))", (name, json.dumps(raw)))
        counts[name] = 1

    runs_dir = state_dir.joinpath(*RUNS_DIR)
    if runs_dir.is_dir():
        counts["runs"] = _import_runs(conn, runs_dir)

    counts.update(import_accounts_file(conn, accounts_file or state_dir.joinpath(*ACCOUNTS_FILE)))
    return counts


def export_state_files(conn: sqlite3.Connection, dest: Path) -> dict[str, int]:
    """Write the database back out in the old file layout (rollback to a relay that predates the database)."""
    counts: dict[str, int] = {}

    def write(parts: tuple[str, ...], data: Any) -> None:
        target = dest.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")

    schedules = [json.loads(b) for (b,) in conn.execute("SELECT body FROM schedules ORDER BY id")]
    next_id = conn.execute("SELECT value FROM meta WHERE key=?", (META_NEXT_SCHEDULE_ID,)).fetchone()
    write(SCHEDULES_FILE, {"next_id": int(next_id[0]) if next_id else 1, "schedules": schedules})
    counts["schedules"] = len(schedules)

    cols = ("hash", "account", "account_key", "device_name", "platform", "created_at", "last_used_at", "expires_at", "push_token", "device_id")
    sessions = [dict(zip(cols, row)) for row in conn.execute(f"SELECT {', '.join(cols)} FROM sessions")]
    write(SESSIONS_FILE, {"sessions": sessions})
    counts["sessions"] = len(sessions)

    for name, parts in DOC_FILES.items():
        row = conn.execute("SELECT body FROM docs WHERE name=?", (name,)).fetchone()
        if row:
            write(parts, json.loads(row[0]))
            counts[name] = 1

    from _push.accounts import AccountStore

    accounts = AccountStore._read(conn)
    write(ACCOUNTS_FILE, accounts)  # includes the plain account secrets: the old relay needs them to log anyone in
    try:
        os.chmod(dest.joinpath(*ACCOUNTS_FILE), 0o600)
    except OSError:
        pass
    counts["accounts"] = len(accounts)

    runs: dict[int, list[str]] = {}
    for schedule_id, body in conn.execute("SELECT schedule_id, body FROM schedule_runs ORDER BY schedule_id, seq"):
        runs.setdefault(schedule_id, []).append(body)
    for schedule_id, bodies in runs.items():
        target = dest.joinpath(*RUNS_DIR, f"{schedule_id}.jsonl")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("".join(b + "\n" for b in bodies), encoding="utf-8")
    counts["runs"] = sum(len(v) for v in runs.values())
    return counts
