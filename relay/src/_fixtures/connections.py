from __future__ import annotations

from contextlib import contextmanager

from . import env


def mysql_connect():
    env.require_live_lab()
    import pymysql

    creds = env.mysql_credentials()
    return pymysql.connect(
        host=creds["host"],
        port=creds["port"],
        user=creds["user"],
        password=creds["password"],
        database=creds["database"],
        autocommit=True,
    )


def postgresql_connect():
    env.require_live_lab()
    import psycopg2

    creds = env.postgresql_credentials()
    conn = psycopg2.connect(
        host=creds["host"],
        port=creds["port"],
        user=creds["user"],
        password=creds["password"],
        dbname=creds["dbname"],
    )
    conn.autocommit = True
    return conn


def mssql_connect():
    env.require_live_lab()
    import pyodbc

    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={env.MSSQL_SERVER};DATABASE={env.MSSQL_DATABASE};"
        "Trusted_Connection=yes;TrustServerCertificate=yes;"
    )
    conn = pyodbc.connect(conn_str, autocommit=True)
    return conn


@contextmanager
def mysql():
    conn = mysql_connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def postgresql():
    conn = postgresql_connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def mssql():
    conn = mssql_connect()
    try:
        yield conn
    finally:
        conn.close()
