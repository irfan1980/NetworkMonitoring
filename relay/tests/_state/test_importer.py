from __future__ import annotations

import json
import sqlite3

import pytest

from _alerts.shared_ack import AckStore
from _push.mutes import MuteStore
from _scheduler.inflight import InflightStore
from _scheduler.store import ScheduleStore
from _session.store import SessionStore
from _state import database as dbmod
from _state.database import DB_FILENAME, Database
from _state.importer import StateImportError, export_state_files
from tests._scheduler.test_store_and_runlog import schedule_input
from tests._state_helpers import read_doc

SCHEDULE = {
    "id": 3, "name": "Nightly", "machine_id": 1, "script": "a.ps1", "args": [], "enabled": True,
    "trigger": {"kind": "weekly", "time": "08:00", "days_of_week": [1, 3]}, "controls": {},
}
SESSION = {
    "hash": "h" * 64, "account": "Ops", "account_key": "k" * 16, "device_name": "Pixel", "platform": "android",
    "created_at": 1.0, "last_used_at": 2.0, "expires_at": 3.0, "push_token": "tok", "device_id": "dev-12345678",
}


def put(tmp_path, parts, data, raw=None):
    path = tmp_path.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw if raw is not None else json.dumps(data), encoding="utf-8")
    return path


def seed_legacy(tmp_path):
    put(tmp_path, ("scheduler", "schedules.json"), {"next_id": 9, "schedules": [SCHEDULE]})
    put(tmp_path, ("auth", "sessions.json"), {"sessions": [SESSION]})
    put(tmp_path, ("scheduler", "inflight.json"), {"last_tick": "2026-01-01T00:00:00Z", "runs": []})
    put(tmp_path, ("push", "mutes.json"), {"machine:4": ["k1"]})
    put(tmp_path, ("push", "acks.json"), {"acks": {"machine:4": {"acknowledged_at": 5.0, "acknowledged_by": "Ops"}}})
    put(tmp_path, ("push", "state.json"), {"alerts": {"machine:4": {"first_seen": 1.0, "sent": {}}}, "last_pushed": {"k": 2.0}})
    runs = json.dumps({"seq": 1, "outcome": "success"}) + "\n" + json.dumps({"seq": 2, "outcome": "failed"}) + "\n"
    put(tmp_path, ("scheduler", "runs", "3.jsonl"), None, raw=runs)


def snapshot(tmp_path):
    return {
        p.relative_to(tmp_path).as_posix(): p.read_bytes()
        for p in tmp_path.rglob("*") if p.is_file() and not p.name.startswith(DB_FILENAME)
    }


def test_existing_files_are_imported_once_and_left_untouched(tmp_path):
    seed_legacy(tmp_path)
    before = snapshot(tmp_path)
    schedules = ScheduleStore(tmp_path)
    assert [s.id for s in schedules.list()] == [3]
    assert schedules.create(schedule_input()).id == 9
    assert SessionStore(tmp_path, lambda: None).count() == 1
    assert read_doc(tmp_path, "push/mutes") == {"machine:4": ["k1"]}
    assert AckStore(tmp_path).is_acked("machine:4")
    assert InflightStore(tmp_path).load() == ([], "2026-01-01T00:00:00Z")
    assert read_doc(tmp_path, "push/state")["last_pushed"] == {"k": 2.0}
    assert Database.for_dir(tmp_path).query("SELECT seq FROM schedule_runs WHERE schedule_id=3 ORDER BY seq") == [(1,), (2,)]
    assert snapshot(tmp_path) == before


def test_files_changed_after_the_import_are_ignored_on_the_next_start(tmp_path):
    seed_legacy(tmp_path)
    ScheduleStore(tmp_path).delete(3)
    dbmod._registry.clear()
    put(tmp_path, ("scheduler", "schedules.json"), {"next_id": 1, "schedules": [SCHEDULE]})
    assert ScheduleStore(tmp_path).list() == []


def test_fresh_install_with_no_files_starts_empty(tmp_path):
    assert ScheduleStore(tmp_path).list() == []
    assert SessionStore(tmp_path, lambda: None).count() == 0
    assert MuteStore(tmp_path).muted_alerts("k") == []


@pytest.mark.parametrize("parts,raw,needle", [
    (("scheduler", "schedules.json"), "{not json", "schedules.json"),
    (("scheduler", "schedules.json"), json.dumps({"schedules": [{"id": 1}]}), "invalid schedule entry"),
    (("scheduler", "schedules.json"), json.dumps({"schedules": [SCHEDULE, SCHEDULE]}), "duplicate"),
    (("scheduler", "schedules.json"), "", "schedules.json"),
    (("auth", "sessions.json"), json.dumps({"sessions": [{"hash": "x"}]}), "invalid session entry"),
    (("auth", "sessions.json"), json.dumps([1, 2]), "expected an object"),
    (("push", "acks.json"), json.dumps({"acks": {"a": {}}}), "acks.json"),
    (("push", "downtime.json"), json.dumps({"windows": [{"id": "x"}]}), "downtime.json"),
    (("push", "mutes.json"), "[]", "mutes.json"),
    (("push", "state.json"), "{truncated", "state.json"),
    (("scheduler", "inflight.json"), json.dumps({"runs": [{"nope": 1}]}), "inflight.json"),
])
def test_an_unreadable_file_stops_the_import_with_a_clear_error_and_imports_nothing(tmp_path, parts, raw, needle):
    seed_legacy(tmp_path)
    bad = put(tmp_path, parts, None, raw=raw)
    before = snapshot(tmp_path)
    with pytest.raises(StateImportError) as err:
        Database.for_dir(tmp_path)
    assert needle in str(err.value) and "left untouched" in str(err.value)
    assert snapshot(tmp_path) == before and bad.read_text(encoding="utf-8") == raw
    check = sqlite3.connect(str(tmp_path / DB_FILENAME))
    for table in ("schedules", "sessions", "docs", "meta", "schedule_runs"):
        assert check.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    check.close()


def test_after_fixing_the_file_the_next_start_imports_everything(tmp_path):
    seed_legacy(tmp_path)
    path = put(tmp_path, ("scheduler", "schedules.json"), None, raw="{broken")
    with pytest.raises(StateImportError):
        Database.for_dir(tmp_path)
    path.write_text(json.dumps({"next_id": 4, "schedules": [SCHEDULE]}), encoding="utf-8")
    assert [s.id for s in ScheduleStore(tmp_path).list()] == [3]
    assert SessionStore(tmp_path, lambda: None).count() == 1


def test_a_torn_run_log_line_is_skipped_but_the_rest_is_imported(tmp_path):
    torn = json.dumps({"seq": 1, "outcome": "success"}) + '\n{"seq": 2, "outc'
    put(tmp_path, ("scheduler", "runs", "5.jsonl"), None, raw=torn)
    put(tmp_path, ("scheduler", "runs", "notanid.jsonl"), None, raw="{}\n")
    assert Database.for_dir(tmp_path).query("SELECT schedule_id, seq FROM schedule_runs") == [(5, 1)]


def test_missing_seq_in_old_run_rows_is_numbered_in_order(tmp_path):
    raw = json.dumps({"outcome": "a"}) + "\n" + json.dumps({"outcome": "b"}) + "\n"
    put(tmp_path, ("scheduler", "runs", "2.jsonl"), None, raw=raw)
    rows = Database.for_dir(tmp_path).query("SELECT seq, body FROM schedule_runs ORDER BY seq")
    assert [(s, json.loads(b)["outcome"]) for s, b in rows] == [(1, "a"), (2, "b")]


def test_stores_persist_across_a_restart(tmp_path):
    created = ScheduleStore(tmp_path).create(schedule_input())
    MuteStore(tmp_path).mute("machine:1", "k")
    AckStore(tmp_path).acknowledge("machine:1", "Ops")
    dbmod._registry.clear()
    assert ScheduleStore(tmp_path).get(created.id) == created
    assert MuteStore(tmp_path).is_muted("machine:1", "k")
    assert AckStore(tmp_path).is_acked("machine:1")


def test_export_round_trips_back_into_the_file_layout_and_reimports_identically(tmp_path):
    seed_legacy(tmp_path / "old")
    db = Database.for_dir(tmp_path / "old")
    out = tmp_path / "export"
    with db.transaction() as conn:
        counts = export_state_files(conn, out)
    assert counts["schedules"] == 1 and counts["sessions"] == 1 and counts["runs"] == 2
    assert json.loads((out / "scheduler" / "schedules.json").read_text())["next_id"] == 9
    again = Database.for_dir(out)
    assert [json.loads(b)["id"] for (b,) in again.query("SELECT body FROM schedules")] == [3]
    assert again.get_doc("push/mutes") == {"machine:4": ["k1"]}


def test_create_app_refuses_to_start_on_a_corrupt_legacy_file(tmp_path, write_config, config_dict):
    from _lib.config import StartupConfigError
    from main import create_app

    state = tmp_path / "state"
    put(state, ("scheduler", "schedules.json"), None, raw="{nope")
    with pytest.raises(StartupConfigError, match="schedules.json"):
        create_app(write_config(config_dict), state_dir=state)
