from __future__ import annotations

from . import env

_SCRATCH_ID = 99999

_REAL_HOST_PORT = {
    "mysql": ("127.0.0.1", 3306),
    "postgresql": ("::1", 5432),
    "mssql": ("localhost", 1433),
}

_CREDS_BY_PLATFORM = {
    "mysql": lambda: env.mysql_credentials(),
    "postgresql": lambda: env.postgresql_credentials(),
}


def _base_entry(platform: str) -> dict:
    env.require_live_lab()
    entry = {
        "id": _SCRATCH_ID,
        "name": f"FIX08-scratch-{platform}",
        "platform": platform,
        "user": "scratch",
        "password": "scratch",
        "database": "scratch",
        "used_buses": [],
    }
    if platform in _CREDS_BY_PLATFORM:
        creds = _CREDS_BY_PLATFORM[platform]()
        entry["user"] = creds.get("user", "scratch")
        entry["password"] = creds.get("password", "scratch")
        entry["database"] = creds.get("database") or creds.get("dbname", "scratch")
    return entry


def refused_server_entry(platform: str) -> dict:
    host, port = _REAL_HOST_PORT[platform]
    entry = _base_entry(platform)
    entry.update(host=host, port=port)
    return entry


def blackhole_server_entry(platform: str) -> dict:
    _, port = _REAL_HOST_PORT[platform]
    entry = _base_entry(platform)
    entry.update(host="192.0.2.1", port=port)
    return entry


def unused_loopback_server_entry(platform: str, port: int) -> dict:
    entry = _base_entry(platform)
    entry.update(host="127.0.0.1", port=port)
    return entry
