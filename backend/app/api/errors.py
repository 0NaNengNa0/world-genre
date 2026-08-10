"""Application-wide error handling.

Without this, a dropped database connection surfaced as FastAPI's default
500: an unhandled traceback in the console and `{"detail": "Internal Server
Error"}` to the caller. That's unhelpful in both directions - the frontend
can't tell a transient outage from a bug, and nothing durable is logged.

Two handlers:
  * psycopg2.Error  -> 503, because a database that's down or refusing
                       connections is a dependency failure the caller should
                       retry, not a malformed request. Returning 500 tells
                       clients and uptime monitors the wrong thing.
  * Exception       -> 500, logged with a traceback so it isn't lost.

Neither ever echoes the exception text. psycopg2's messages embed the host,
port and user from the connection string, and these endpoints are
unauthenticated.
"""
import logging

import psycopg2
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger("api")


async def database_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "database error on %s %s: %s", request.method, request.url.path, type(exc).__name__
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": "The database is currently unavailable. Please try again shortly.",
            "error": type(exc).__name__,
        },
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # exc_info so the traceback is actually recorded - the default handler
    # prints it to stderr and it's gone.
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error", "error": type(exc).__name__},
    )


def register_error_handlers(app: FastAPI) -> None:
    # psycopg2.Error is the base class for every driver error, so this covers
    # OperationalError, InterfaceError and the rest in one registration.
    app.add_exception_handler(psycopg2.Error, database_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
