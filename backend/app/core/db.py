"""Postgres connection helper - the warehouse the "load" stage
(scripts/run_load.py) writes to and the API reads from. One place to
answer "how do we talk to the database," same convention as config.py
for settings/seed data.

Deliberately plain psycopg2, not an ORM - the point of this project's SQL
layer is to write and show real SQL (see sql/schema.sql and
sql/queries/), not to have an ORM generate it.

Connections come from a pool rather than being opened per call. Opening one
per request measured 2.30ms against 0.10ms for a reused connection - a ~23x
overhead, and that was the best case: a unix socket on the same machine with
no TLS. A managed Postgres adds network round-trips and a TLS handshake to
every one of those, on an endpoint whose actual queries take under a
millisecond.
"""
import os
import threading
from contextlib import contextmanager

import psycopg2
from psycopg2 import pool as pg_pool

# Falls back to the docker-compose app-postgres service's host-mapped port,
# so this works out of the box even without a .env - matches the pattern
# app/core/config.py uses for seed data (sane default, .env overrides it).
_DEFAULT_DATABASE_URL = "postgresql://world_genre:world_genre@localhost:5433/world_genre"

# uvicorn serves requests from a thread pool, so the pool has to be
# thread-safe; ThreadedConnectionPool is the psycopg2 one that is.
_MIN_CONNECTIONS = 1
# Comfortably above uvicorn's default worker threads while staying far below
# Postgres's default max_connections of 100, which the Airflow containers also
# draw on.
_MAX_CONNECTIONS = 10

_pool: pg_pool.ThreadedConnectionPool | None = None
_pool_url: str | None = None
_pool_lock = threading.Lock()


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DATABASE_URL)


def _get_pool() -> pg_pool.ThreadedConnectionPool:
    """The process-wide pool, created on first use.

    Rebuilt if DATABASE_URL changes rather than captured once at import:
    the test suite points each test at a throwaway database via
    monkeypatch.setenv, and a pool pinned to the first URL it ever saw would
    quietly keep serving connections to the wrong one.
    """
    global _pool, _pool_url
    url = _database_url()
    with _pool_lock:
        if _pool is None or _pool_url != url:
            if _pool is not None:
                _pool.closeall()
            _pool = pg_pool.ThreadedConnectionPool(_MIN_CONNECTIONS, _MAX_CONNECTIONS, url)
            _pool_url = url
        return _pool


def close_pool() -> None:
    """Close every pooled connection. Called on API shutdown; harmless if no
    pool was ever created."""
    global _pool, _pool_url
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None
            _pool_url = None


@contextmanager
def get_connection():
    """Yields a pooled connection; commits on clean exit, rolls back on error.

    Usage is unchanged from when this opened a fresh connection each time:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(...)

    The connection is RETURNED to the pool rather than closed, so callers
    must not hold onto it past the `with` block.
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        # A connection can be handed back mid-transaction or after a server
        # error; rolling back before returning it stops the next borrower
        # inheriting a poisoned transaction ("current transaction is aborted").
        try:
            conn.rollback()
        except psycopg2.Error:
            pass
        raise
    finally:
        pool.putconn(conn)
