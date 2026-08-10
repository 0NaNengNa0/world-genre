"""Shared pytest fixtures.

pg_database_url spins up a throwaway, self-contained Postgres instance
(via pgserver - a real Postgres binary bundled in the pip package, no
Docker or system install needed) for the DB-layer tests in
tests/test_db_integration.py. Session-scoped so the ~1s server startup
cost is paid once per test run, not once per test.
"""
import psycopg2
import pytest


@pytest.fixture(scope="session")
def _pg_server(tmp_path_factory):
    pgserver = pytest.importorskip("pgserver")
    data_dir = tmp_path_factory.mktemp("pgdata")
    server = pgserver.get_server(str(data_dir))
    yield server
    server.cleanup()


@pytest.fixture()
def pg_database_url(_pg_server, monkeypatch):
    """Creates a fresh database per test (so tests don't see each other's
    rows) inside the shared throwaway server, and points DATABASE_URL at
    it via monkeypatch (auto-reverted after the test)."""
    base_uri = _pg_server.get_uri()
    db_name = "test_" + str(id(object()))  # cheap unique-enough name per test

    admin_conn = psycopg2.connect(base_uri)
    admin_conn.autocommit = True
    with admin_conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE {db_name}")
    admin_conn.close()

    db_url = base_uri.replace("/postgres?", f"/{db_name}?")
    monkeypatch.setenv("DATABASE_URL", db_url)
    yield db_url

    # Return every pooled connection before dropping the database. Postgres
    # refuses to drop a database that still has clients attached and simply
    # blocks, which hangs the whole test run rather than failing - so this
    # isn't tidiness, it's what keeps the suite from deadlocking.
    from app.core.db import close_pool

    close_pool()

    admin_conn = psycopg2.connect(base_uri)
    admin_conn.autocommit = True
    with admin_conn.cursor() as cur:
        cur.execute(f"DROP DATABASE {db_name}")
    admin_conn.close()
