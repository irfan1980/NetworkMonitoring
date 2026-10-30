from __future__ import annotations

from pathlib import Path

RELAY_ROOT = Path(__file__).resolve().parents[2]


def resolve_push_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (RELAY_ROOT / path).resolve()


def resolve_accounts_path(accounts_file: str, state_dir) -> Path:
    if not accounts_file:
        return (Path(state_dir) / "auth" / "push_accounts.json").resolve()
    return resolve_push_path(accounts_file)
