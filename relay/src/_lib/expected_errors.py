from __future__ import annotations

import asyncio
import socket

from _db.errors import (
    MYSQL_HOST_BLOCKED_CODE,
    classify_mssql_error,
    classify_mssql_odbc_error,
    classify_mysql_error,
    classify_postgresql_error,
    mysql_error_code,
)

try:  # pragma: no cover
    from _db.mssql_odbc import MssqlOdbcError
except Exception:  # pragma: no cover
    class MssqlOdbcError(RuntimeError):  # type: ignore[no-redef]
        pass

_CLASSIFIERS = {
    "mysql": classify_mysql_error,
    "postgresql": classify_postgresql_error,
    "mssql": classify_mssql_error,
}

_EXPECTED_CLASSIFICATIONS = {"network", "auth"}


def _is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, (asyncio.TimeoutError, TimeoutError))


def describe_driver_error(exc: BaseException, platform: str, label: str) -> tuple[bool, str]:
    if _is_timeout(exc):
        return True, f"{label} timed out"

    if isinstance(exc, socket.gaierror):
        return True, f"{label} unreachable: DNS lookup failed ({exc})"

    if platform == "mssql" and isinstance(exc, MssqlOdbcError):
        classification = classify_mssql_odbc_error(str(exc))
        if classification == "auth":
            return True, f"{label} authentication rejected"
        if classification == "network":
            return True, f"{label} unreachable: {exc}"
        return False, str(exc)

    classifier = _CLASSIFIERS.get(platform)
    if classifier is None:
        return False, str(exc)

    try:
        classification = classifier(exc)
    except Exception:
        return False, str(exc)

    if classification not in _EXPECTED_CLASSIFICATIONS:
        return False, str(exc)
    if classification == "auth":
        return True, f"{label} authentication rejected: {exc}"
    if platform == "mysql" and mysql_error_code(exc) == MYSQL_HOST_BLOCKED_CODE:
        return True, (
            f"{label} blocked by the server after repeated connection failures "
            f"-- run FLUSH HOSTS on this MySQL server once the cause (wrong "
            f"credentials, wrong port, or the host being genuinely unreachable) "
            f"is fixed"
        )
    return True, f"{label} unreachable: {exc}"
