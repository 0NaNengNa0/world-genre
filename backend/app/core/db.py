"""Postgres connection helper - the warehouse the "load" stage
(scripts/run_load.py) writes to and the API reads from. One place to
answer "how do we talk to the database," same convention as config.py
for settings/seed data.

Deliberately plain psycopg2, not an ORM - the point of this project's SQL
layer is to write and show real SQL (see sql/schema.sql and
sql/queries/), not to have an ORM generate it.
"""
import os
from contextlib import contextmanager

import psycopg2

# Falls back to the docker-compose app-postgres service's host-mapped port,
# so this works out of the box even without a .env - matches the pattern
# app/core/config.py uses for seed data (sane default, .env overrides it).
_DEFAULT_DATABASE_URL = "postgresql://world_genre:world_genre@localhost:5433/world_genre"


@contextmanager
def get_connection():
    """Yields a connection; commits on clean exit, rolls back on exception.

    Usage:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(...)

    Reads DATABASE_URL from the environment on every call (not once at
    import time) so tests can point this at a throwaway database via
    monkeypatch.setenv without needing to reload this module - see
    tests/conftest.py.
    """
    conn = psycopg2.connect(os.environ.get("DATABASE_URL", _DEFAULT_DATABASE_URL))
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
