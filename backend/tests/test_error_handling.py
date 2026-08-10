"""Tests for the API's error handling.

Exercised through the real ASGI app rather than by calling the handlers
directly, because what matters is that FastAPI actually routes a psycopg2
failure to the 503 handler - registering a handler for the wrong exception
class is a mistake that unit-testing the handler in isolation would miss
entirely.
"""
import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import register_error_handlers


@pytest.fixture()
def client():
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/db-down")
    def db_down():
        raise psycopg2.OperationalError(
            'connection to server at "10.0.0.5", port 5432 failed: '
            'password authentication failed for user "world_genre"'
        )

    @app.get("/interface-error")
    def interface_error():
        # A different psycopg2 subclass - the handler registers the base
        # class, so this must be covered by the same registration.
        raise psycopg2.InterfaceError("connection already closed")

    @app.get("/boom")
    def boom():
        raise ValueError("something unexpected")

    # raise_server_exceptions=False makes TestClient return the handler's
    # response instead of re-raising, which is what a real client would see.
    return TestClient(app, raise_server_exceptions=False)


class TestDatabaseErrors:
    def test_returns_503_not_500(self, client):
        # A database outage is a dependency failure the caller can retry, not
        # a bug in the request. 500 would tell clients and uptime monitors
        # the wrong thing.
        response = client.get("/db-down")
        assert response.status_code == 503

    def test_names_the_error_class_without_leaking_the_connection_string(self, client):
        response = client.get("/db-down")
        body = response.json()
        assert body["error"] == "OperationalError"
        serialized = response.text
        # psycopg2's message embeds host, port and username; these endpoints
        # are unauthenticated, so none of it may be echoed back.
        assert "10.0.0.5" not in serialized
        assert "5432" not in serialized
        assert "world_genre" not in serialized
        assert "password" not in serialized

    def test_message_is_actionable(self, client):
        assert "try again" in client.get("/db-down").json()["detail"].lower()

    def test_other_psycopg2_subclasses_are_handled_too(self, client):
        response = client.get("/interface-error")
        assert response.status_code == 503
        assert response.json()["error"] == "InterfaceError"


class TestUnhandledErrors:
    def test_returns_500(self, client):
        assert client.get("/boom").status_code == 500

    def test_does_not_leak_the_exception_message(self, client):
        response = client.get("/boom")
        assert "something unexpected" not in response.text
        assert response.json()["error"] == "ValueError"

    def test_logs_with_a_traceback(self, client, caplog):
        with caplog.at_level("ERROR", logger="api"):
            client.get("/boom")
        record = next(r for r in caplog.records if r.name == "api")
        # exc_info is what preserves the traceback; without it the default
        # handler prints to stderr and the detail is lost.
        assert record.exc_info is not None
        assert "/boom" in record.getMessage()
