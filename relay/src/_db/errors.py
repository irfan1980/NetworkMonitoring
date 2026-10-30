from __future__ import annotations

Classification = str


_MYSQL_AUTH_CODES = {
    1045,
    1044,
    3118,
    1698,
}
_MYSQL_NETWORK_CODES = {
    2003,
    2002,
    2013,
    2006,
    1129,
    1130,
    1226,
    1203,
}

MYSQL_HOST_BLOCKED_CODE = 1129


def classify_mysql_error(exc: BaseException) -> Classification:
    code = _pymysql_code(exc)
    if code in _MYSQL_AUTH_CODES:
        return "auth"
    if code in _MYSQL_NETWORK_CODES:
        return "network"
    return "query"


def _pymysql_code(exc: BaseException) -> int | None:
    args = getattr(exc, "args", None)
    if args and isinstance(args[0], int):
        return args[0]
    return None


def mysql_error_code(exc: BaseException) -> int | None:
    return _pymysql_code(exc)


_POSTGRES_AUTH_SQLSTATES = {
    "28P01",
    "28000",
}
_POSTGRES_NETWORK_SQLSTATES = {
    "3D000",
    "08001",
    "08006",
    "57P03",
    "08004",
    "53300",
}


def classify_postgresql_error(exc: BaseException) -> Classification:
    pgcode = getattr(exc, "pgcode", None)
    if pgcode in _POSTGRES_AUTH_SQLSTATES:
        return "auth"
    if pgcode in _POSTGRES_NETWORK_SQLSTATES:
        return "network"
    if pgcode is None and type(exc).__name__ == "OperationalError":
        return "network"
    return "query"


_MSSQL_AUTH_SQLSTATES = {"28000"}
_MSSQL_NETWORK_SQLSTATES = {"08001", "08S01", "HYT00", "HYT01"}


def classify_mssql_error(exc: BaseException) -> Classification:
    sqlstate = _pyodbc_sqlstate(exc)
    if sqlstate in _MSSQL_AUTH_SQLSTATES:
        return "auth"
    if sqlstate in _MSSQL_NETWORK_SQLSTATES:
        return "network"
    return "query"


def classify_mssql_odbc_error(stderr_text: str) -> Classification:
    text = stderr_text.lower()
    if "login failed" in text or "cannot open user default database" in text:
        return "auth"
    if "network-related" in text or "could not open a connection" in text or "timeout expired" in text:
        return "network"
    return "query"


def _pyodbc_sqlstate(exc: BaseException) -> str | None:
    args = getattr(exc, "args", None)
    if args and isinstance(args[0], str):
        return args[0]
    return None
