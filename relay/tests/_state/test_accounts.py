from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess

import pytest
from fastapi.testclient import TestClient

import state_db
from _push.accounts import AccountStore
from _state import database as dbmod
from _state.database import ACCOUNTS_MARKER, DB_FILENAME, IMPORT_MARKER, MIGRATIONS, Database
from _state.importer import StateImportError
from main import create_app
from tests._accounts_helpers import get_accounts, set_accounts

LEGACY = [
    {"account_secret": "legacy-secret-1", "name": "Ops", "min_severity": 2, "device_codes": [
        {"token": "tok-1", "platform": "web", "device_name": "Chrome", "registered_at": "2026-01-01T00:00:00Z",
         "last_seen_at": "2026-01-02T00:00:00Z", "device_id": "dev-12345678"}, "plain-token"],
     "push_window": {"timezone": "UTC", "days": [0, 1], "start": "09:00", "end": "17:00"}},
    {"account_secret": "legacy-secret-2", "name": "Boss", "min_severity": 5, "device_codes": []},
]


def put_legacy(state, data=LEGACY, raw=None):
    path = state / "auth" / "push_accounts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw if raw is not None else json.dumps(data), encoding="utf-8")
    return path


def make_app(tmp_path, write_config, config_dict):
    return create_app(write_config(config_dict), state_dir=tmp_path / "state")


def login(client, secret, device="d"):
    return client.post("/api/auth/login", json={"account_secret": secret, "platform": "web", "device_name": device})


def operator(tmp_path) -> sqlite3.Connection:
    return sqlite3.connect(str(tmp_path / "state" / DB_FILENAME), timeout=10)


def test_user_accounts_has_exactly_the_three_columns_the_operator_inserts(tmp_path):
    db = Database.for_dir(tmp_path)
    cols = [r[1] for r in db.query("PRAGMA table_info(user_accounts)")]
    assert cols == ["account_secret", "name", "min_severity"]
    db.write(lambda c: c.execute("INSERT INTO user_accounts VALUES ('abc123xyz', 'Tabish', 5)"))
    db.write(lambda c: c.execute("INSERT INTO user_accounts (account_secret, name) VALUES ('second', 'Two')"))
    assert [(a["name"], a["min_severity"]) for a in AccountStore(db).accounts()] == [("Tabish", 5), ("Two", 5)]


def test_constraints_reject_bad_rows(tmp_path):
    db = Database.for_dir(tmp_path)
    db.write(lambda c: c.execute("INSERT INTO user_accounts VALUES ('s', 'n', 3)"))
    for sql in (
        "INSERT INTO user_accounts VALUES ('s', 'other', 3)",      # duplicate secret
        "INSERT INTO user_accounts VALUES ('', 'n', 3)",           # empty secret
        "INSERT INTO user_accounts VALUES ('t', '', 3)",           # empty name
        "INSERT INTO user_accounts VALUES ('t', 'n', 0)",          # severity out of range
        "INSERT INTO user_accounts VALUES ('t', 'n', 6)",
        "INSERT INTO user_accounts VALUES ('t', NULL, 3)",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            db.write(lambda c, sql=sql: c.execute(sql))


def test_an_account_added_from_another_connection_can_log_in_on_the_next_request(tmp_path, write_config, config_dict):
    app = make_app(tmp_path, write_config, config_dict)
    client = TestClient(app)
    assert login(client, "new-secret").status_code == 401
    app.state.login_rate_limiter._clients.clear()  # the failed probe above must not lock the next try
    op = operator(tmp_path)
    op.execute("INSERT INTO user_accounts VALUES ('new-secret', 'Tabish', 5)")
    op.commit()
    op.close()
    resp = login(client, "new-secret")
    assert resp.status_code == 200 and resp.json()["account"] == "Tabish" and resp.json()["min_severity"] == 5


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="no sqlite3 CLI installed")
def test_the_real_sqlite3_cli_adds_and_removes_an_account(tmp_path, write_config, config_dict):
    app = make_app(tmp_path, write_config, config_dict)
    client = TestClient(app)
    db = str(tmp_path / "state" / DB_FILENAME)
    run = lambda sql: subprocess.run(["sqlite3", db, sql], capture_output=True, text=True, timeout=30)  # noqa: E731
    assert run("INSERT INTO user_accounts VALUES ('cli-secret', 'Cli', 4);").returncode == 0
    ok = login(client, "cli-secret")
    assert ok.status_code == 200 and ok.json()["min_severity"] == 4
    headers = {"Authorization": f"Bearer {ok.json()['token']}"}
    assert run("DELETE FROM user_accounts WHERE name = 'Cli';").returncode == 0
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert login(client, "cli-secret").status_code == 401


def test_removing_an_account_by_sql_stops_login_and_ends_its_sessions_and_drops_its_devices(tmp_path, write_config, config_dict):
    app = make_app(tmp_path, write_config, config_dict)
    client = TestClient(app)
    set_accounts(tmp_path / "state", LEGACY)
    app.state.login_rate_limiter._clients.clear()
    token = login(client, "legacy-secret-1").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/push/register", headers=headers, json={"token": "tok-9", "platform": "web", "device_name": "x"}).status_code == 200
    assert client.put("/api/push/window", headers=headers, json={"timezone": "UTC", "days": [0], "start": "01:00", "end": "02:00"}).status_code == 200
    op = operator(tmp_path)
    op.execute("DELETE FROM user_accounts WHERE account_secret = 'legacy-secret-1'")
    op.commit()
    assert op.execute("SELECT COUNT(*) FROM account_devices").fetchone()[0] == 0
    assert op.execute("SELECT COUNT(*) FROM account_push_windows").fetchone()[0] == 0
    op.close()
    assert client.get("/api/auth/me", headers=headers).status_code == 401  # session ends on its next use
    assert login(client, "legacy-secret-1").status_code == 401
    assert login(client, "legacy-secret-2").status_code == 200


def test_changing_a_secret_by_sql_keeps_devices_but_ends_the_old_sessions(tmp_path, write_config, config_dict):
    app = make_app(tmp_path, write_config, config_dict)
    client = TestClient(app)
    set_accounts(tmp_path / "state", LEGACY)
    headers = {"Authorization": f"Bearer {login(client, 'legacy-secret-2').json()['token']}"}
    client.post("/api/push/register", headers=headers, json={"token": "tok-b", "platform": "web", "device_name": "x"})
    op = operator(tmp_path)
    op.execute("UPDATE user_accounts SET account_secret = 'rotated' WHERE name = 'Boss'")
    op.commit()
    op.close()
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert [d["token"] for a in get_accounts(tmp_path / "state") if a["name"] == "Boss" for d in a["device_codes"]] == ["tok-b"]
    assert login(client, "rotated").status_code == 200


def test_min_severity_edited_by_sql_applies_without_a_new_login(tmp_path, write_config, config_dict):
    app = make_app(tmp_path, write_config, config_dict)
    client = TestClient(app)
    set_accounts(tmp_path / "state", LEGACY)
    headers = {"Authorization": f"Bearer {login(client, 'legacy-secret-2').json()['token']}"}
    op = operator(tmp_path)
    op.execute("UPDATE user_accounts SET min_severity = 1 WHERE name = 'Boss'")
    op.commit()
    op.close()
    assert client.get("/api/auth/me", headers=headers).json()["min_severity"] == 1


def test_legacy_file_is_imported_with_tokens_and_windows_and_left_untouched(tmp_path):
    state = tmp_path / "state"
    path = put_legacy(state)
    before = path.read_bytes()
    db = Database.for_dir(state)
    accounts = AccountStore(db).accounts()
    assert [(a["name"], a["min_severity"]) for a in accounts] == [("Ops", 2), ("Boss", 5)]
    assert [d["token"] for d in accounts[0]["device_codes"]] == ["tok-1", "plain-token"]
    assert accounts[0]["device_codes"][0]["device_id"] == "dev-12345678" and accounts[0]["push_window"]["days"] == [0, 1]
    assert path.read_bytes() == before
    # the file is no longer read: later edits to it change nothing
    path.write_text(json.dumps([{"account_secret": "edited", "name": "Edited"}]))
    dbmod._registry.clear()
    assert [a["name"] for a in AccountStore(Database.for_dir(state)).accounts()] == ["Ops", "Boss"]


def test_a_custom_accounts_file_setting_is_used_for_the_import_only(tmp_path):
    custom = tmp_path / "elsewhere" / "accounts.json"
    custom.parent.mkdir()
    custom.write_text(json.dumps(LEGACY))
    put_legacy(tmp_path / "state", [{"account_secret": "default-path", "name": "Default"}])
    db = Database.for_dir(tmp_path / "state", accounts_file=custom)
    assert [a["name"] for a in AccountStore(db).accounts()] == ["Ops", "Boss"]


def test_a_database_made_before_accounts_moved_in_picks_them_up_through_its_own_marker(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    # an earlier release: schema version 1, files already imported, no account tables
    old = sqlite3.connect(str(state / DB_FILENAME))
    for statement in MIGRATIONS[0]:
        old.execute(statement)
    old.execute("PRAGMA user_version=1")
    old.execute("INSERT INTO meta(key, value) VALUES (?, '1')", (IMPORT_MARKER,))
    old.execute("INSERT INTO docs(name, body, updated_at) VALUES ('push/mutes', '{}', 0)")
    old.commit()
    old.close()
    path = put_legacy(state)
    before = path.read_bytes()
    db = Database.for_dir(state)
    assert db.query("PRAGMA user_version")[0][0] == len(MIGRATIONS)
    assert len(AccountStore(db).accounts()) == 2
    assert db.query("SELECT 1 FROM meta WHERE key=?", (ACCOUNTS_MARKER,))
    assert db.get_doc("push/mutes") == {}
    assert path.read_bytes() == before
    # and only once: removing every account by SQL is not undone by the next start
    db.write(lambda c: c.execute("DELETE FROM user_accounts"))
    dbmod._registry.clear()
    assert AccountStore(Database.for_dir(state)).accounts() == []


def test_an_old_database_without_the_accounts_file_just_gets_empty_account_tables(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    old = sqlite3.connect(str(state / DB_FILENAME))
    for statement in MIGRATIONS[0]:
        old.execute(statement)
    old.execute("PRAGMA user_version=1")
    old.execute("INSERT INTO meta(key, value) VALUES (?, '1')", (IMPORT_MARKER,))
    old.commit()
    old.close()
    assert AccountStore(Database.for_dir(state)).accounts() == []


@pytest.mark.parametrize("raw,needle", [
    ("{broken", "push_accounts.json"),
    ('{"not": "a list"}', "JSON list"),
    ('[{"name": "no secret"}]', "no account_secret"),
    ('[{"account_secret": "a", "name": "x"}, {"account_secret": "a", "name": "y"}]', "repeats"),
    ("", "push_accounts.json"),
])
def test_a_bad_accounts_file_stops_the_import_and_imports_nothing(tmp_path, raw, needle):
    state = tmp_path / "state"
    put_legacy(state, raw=raw)
    with pytest.raises(StateImportError, match=needle) as err:
        Database.for_dir(state)
    assert "left untouched" in str(err.value)
    check = sqlite3.connect(str(state / DB_FILENAME))
    assert check.execute("SELECT COUNT(*) FROM user_accounts").fetchone()[0] == 0
    assert check.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 0
    check.close()
    (state / "auth" / "push_accounts.json").write_text(json.dumps(LEGACY))
    assert len(AccountStore(Database.for_dir(state)).accounts()) == 2


def test_a_bad_accounts_file_on_an_old_database_refuses_and_retries_after_the_fix(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    old = sqlite3.connect(str(state / DB_FILENAME))
    for statement in MIGRATIONS[0]:
        old.execute(statement)
    old.execute("PRAGMA user_version=1")
    old.execute("INSERT INTO meta(key, value) VALUES (?, '1')", (IMPORT_MARKER,))
    old.commit()
    old.close()
    put_legacy(state, raw="{oops")
    with pytest.raises(StateImportError):
        Database.for_dir(state)
    put_legacy(state)
    assert len(AccountStore(Database.for_dir(state)).accounts()) == 2


def test_state_db_check_reports_counts_but_never_secrets(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RELAY_STATE_DIR", str(tmp_path))
    set_accounts(tmp_path, LEGACY)
    assert state_db.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "user_accounts: 2" in out and "account_devices: 2" in out
    assert "legacy-secret" not in out and "tok-1" not in out


def test_state_db_export_writes_the_accounts_with_secrets_privately_and_warns(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RELAY_STATE_DIR", str(tmp_path / "s"))
    set_accounts(tmp_path / "s", LEGACY)
    out_dir = tmp_path / "export"
    assert state_db.main(["export", str(out_dir)]) == 0
    printed = capsys.readouterr().out
    assert "contains account secrets" in printed and "legacy-secret" not in printed
    exported = json.loads((out_dir / "auth" / "push_accounts.json").read_text())
    assert [a["account_secret"] for a in exported] == ["legacy-secret-1", "legacy-secret-2"]
    if os.name != "nt":
        assert (out_dir / "auth" / "push_accounts.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_database_file_is_private(tmp_path):
    Database.for_dir(tmp_path)
    assert (tmp_path / DB_FILENAME).stat().st_mode & 0o777 == 0o600
