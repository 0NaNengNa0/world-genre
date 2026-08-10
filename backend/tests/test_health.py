"""Tests for /api/health.

The DB-down case is the one that matters. The endpoint previously returned
{"status": "ok"} unconditionally, so it stayed green while every other
endpoint 500'd - a load balancer would have kept sending traffic to an
instance that could not answer a single request. A health check is only
worth having if it can fail, so that's what's pinned here.
"""
import psycopg2
import pytest
from fastapi import Response

from app.api.routes.health import get_health


class TestHealthWithDatabase:
    def test_reports_ok_when_the_database_answers(self, pg_database_url):
        response = Response()
        result = get_health(response)
        assert result.status == "ok"
        assert result.database == "ok"
        assert response.status_code in (None, 200)

    def test_actually_queries_the_database(self, pg_database_url, monkeypatch):
        # Guards against the check regressing into a hardcoded "ok": if the
        # connection helper is never called, this fails.
        called = {"n": 0}
        import app.api.routes.health as health_module

        real = health_module.get_connection

        def counting(*args, **kwargs):
            called["n"] += 1
            return real(*args, **kwargs)

        monkeypatch.setattr(health_module, "get_connection", counting)
        get_health(Response())
        assert called["n"] == 1


class TestHealthWhenDatabaseIsDown:
    @pytest.fixture()
    def unreachable_db(self, monkeypatch):
        # Port 1 is reserved and nothing listens there, so this fails fast
        # instead of waiting on a connect timeout.
        monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/nothing")
        from app.core.db import close_pool

        close_pool()
        yield
        close_pool()

    def test_returns_503_and_says_which_dependency_failed(self, unreachable_db):
        response = Response()
        result = get_health(response)

        assert response.status_code == 503
        assert result.status == "degraded"
        assert "unreachable" in result.database

    def test_does_not_raise(self, unreachable_db):
        # The endpoint must report the failure, not propagate it - an
        # exception here would surface as a 500 with a stack trace and tell a
        # monitor nothing useful.
        get_health(Response())

    def test_does_not_leak_the_connection_string(self, unreachable_db):
        # The detail names the exception class only. psycopg2's own error
        # text includes host and port, which shouldn't be echoed to an
        # unauthenticated endpoint.
        result = get_health(Response())
        assert "127.0.0.1" not in result.database
        assert "nobody" not in result.database
        assert result.database == f"unreachable ({psycopg2.OperationalError.__name__})"
