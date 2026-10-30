from _db.errors import (
    classify_mssql_error,
    classify_mssql_odbc_error,
    classify_mysql_error,
    classify_postgresql_error,
)


class _FakeExc(Exception):
    def __init__(self, *args, pgcode=None):
        super().__init__(*args)
        self.pgcode = pgcode


def test_mysql_account_locked_classifies_auth():
    assert classify_mysql_error(_FakeExc(3118, "Account is locked")) == "auth"


def test_mysql_access_denied_classifies_auth():
    assert classify_mysql_error(_FakeExc(1045, "Access denied")) == "auth"


def test_mysql_conn_host_error_classifies_network():
    assert classify_mysql_error(_FakeExc(2003, "Can't connect")) == "network"


def test_mysql_unknown_code_classifies_query():
    assert classify_mysql_error(_FakeExc(1064, "syntax error")) == "query"


def test_postgres_invalid_password_classifies_auth():
    assert classify_postgresql_error(_FakeExc("bad password", pgcode="28P01")) == "auth"


def test_postgres_connection_failure_classifies_network():
    assert classify_postgresql_error(_FakeExc("conn failure", pgcode="08006")) == "network"


def test_postgres_operational_error_with_no_pgcode_classifies_network():
    class OperationalError(_FakeExc):
        pass

    assert classify_postgresql_error(OperationalError("could not connect")) == "network"


def test_postgres_unknown_pgcode_classifies_query():
    assert classify_postgresql_error(_FakeExc("bad syntax", pgcode="42601")) == "query"


def test_mssql_login_failed_classifies_auth():
    assert classify_mssql_error(_FakeExc("28000", "Login failed for user")) == "auth"


def test_mssql_network_related_classifies_network():
    assert classify_mssql_error(_FakeExc("08001", "network-related")) == "network"


def test_mssql_odbc_stderr_login_failed_classifies_auth():
    assert classify_mssql_odbc_error("Login failed for user 'sync_user_odbc'.") == "auth"


def test_mssql_odbc_stderr_network_related_classifies_network():
    assert classify_mssql_odbc_error("A network-related or instance-specific error occurred") == "network"


def test_mssql_odbc_stderr_unrelated_text_classifies_query():
    assert classify_mssql_odbc_error("Invalid column name 'foo'.") == "query"


def test_mssql_odbc_stderr_never_needs_a_real_exception_object():
    result = classify_mssql_odbc_error("")
    assert result == "query"
