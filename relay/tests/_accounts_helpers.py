from __future__ import annotations

from pathlib import Path

from _push.accounts import AccountStore, _normalise_account
from _state.database import Database


def get_accounts(state_dir: Path | str) -> list[dict]:
    db = Database.for_dir(state_dir)
    db.flush()
    return db.read(AccountStore._read)


def set_accounts(state_dir: Path | str, accounts: list[dict]) -> None:
    """Replace every account (what the operator does with SQL), including tokens and push windows."""
    db = Database.for_dir(state_dir)
    normalised = [a for a in (_normalise_account(x) for x in accounts) if a]

    def apply(conn):
        conn.execute("DELETE FROM user_accounts")
        for a in normalised:
            conn.execute("INSERT INTO user_accounts VALUES (?, ?, ?)", (a["account_secret"], a["name"], a["min_severity"]))
        AccountStore.write_children(conn, normalised)

    db.write(apply)
