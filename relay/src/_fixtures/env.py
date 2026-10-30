from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_MAKER_DIR = REPO_ROOT / "config_maker"


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


class LiveLabDisabledError(RuntimeError):
    pass


def live_lab_enabled() -> bool:
    return os.environ.get("RELAY_LIVE_LAB") == "1"


def require_live_lab() -> None:
    # the lab fixtures seed rows and stop real services, so nothing touches live infrastructure unless explicitly asked to
    if not live_lab_enabled():
        raise LiveLabDisabledError("live-lab fixtures are disabled; set RELAY_LIVE_LAB=1 to run them against the real lab")


def _service_database(platform: str) -> dict:
    require_live_lab()
    matches = sorted((CONFIG_MAKER_DIR / "configs").glob(f"*_{platform}-local.json"))
    if not matches:
        raise FileNotFoundError(f"no {platform}-local service config in {CONFIG_MAKER_DIR / 'configs'}; run config_maker/make_config.py")
    return json.loads(matches[0].read_text(encoding="utf-8"))["database"]


def mysql_credentials() -> dict:
    d = _service_database("mysql")
    return {k: d[k] for k in ("host", "port", "user", "password", "database")}


def postgresql_credentials() -> dict:
    d = _service_database("postgresql")
    return {"host": d["host"], "port": d["port"], "user": d["user"], "password": d["password"], "dbname": d["database"]}


MSSQL_DATABASE = "mssql_test_for_service_bus"
MSSQL_SERVER = "localhost"
