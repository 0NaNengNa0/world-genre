"""Exception handlers that fail safely and say something useful.

One handler, for anything unhandled. The database-specific handler this
replaced existed because psycopg2 error text embeds the connection string -
host, port, sometimes the user - and returning it would have leaked
infrastructure detail to anyone who could trigger an error. There is no
database in the request path any more, but the reasoning still applies to
storage errors, which embed bucket names and project IDs.

So the response body never echoes exception text. The class name goes to the
client (enough to distinguish a timeout from a permission error), and the full
traceback goes to the log, where it belongs.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # logger.exception, not logger.error: this is the only place the traceback
    # survives, since it is deliberately not in the response.
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal error ({type(exc).__name__})"},
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(Exception, unhandled_error_handler)
