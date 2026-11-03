from __future__ import annotations

import json
from pathlib import Path

from _state.database import Database


def read_doc(state_dir: Path | str, name: str):
    db = Database.for_dir(state_dir)
    db.flush()
    return db.get_doc(name)


def dump_state_text(state_dir: Path | str) -> str:
    db = Database.for_dir(state_dir)
    db.flush()
    parts = [body for (body,) in db.query("SELECT body FROM docs")]
    parts += [json.dumps(row) for row in db.query("SELECT * FROM sessions")]
    parts += [body for (body,) in db.query("SELECT body FROM schedules")]
    return "\n".join(parts)
