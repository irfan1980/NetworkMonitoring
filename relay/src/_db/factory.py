from __future__ import annotations

from typing import TYPE_CHECKING, Union

from _db.mssql import MSSQLDriver
from _db.mssql_odbc import MSSQLOdbcDriver
from _db.mysql import MySQLDriver
from _db.postgresql import PostgreSQLDriver

if TYPE_CHECKING:
    from _contract.schema import ServerEntry, Settings

AnyDriver = Union[MySQLDriver, PostgreSQLDriver, MSSQLDriver, MSSQLOdbcDriver]


def build_driver(server: "ServerEntry", settings: "Settings") -> AnyDriver:
    if server.platform == "mysql":
        return MySQLDriver(server, settings)
    if server.platform == "postgresql":
        return PostgreSQLDriver(server, settings)
    if server.platform == "mssql":
        if server.driver == "odbc":
            return MSSQLOdbcDriver(server, settings)
        return MSSQLDriver(server, settings)
    raise ValueError(f"build_driver: unknown platform {server.platform!r}")
