"""Liveness/readiness endpoint.

This used to return {"status": "ok"} unconditionally, which made it worse
than useless: with Postgres down, every real endpoint returned 500 while
this still reported healthy - so a load balancer would keep routing traffic
to an instance that could not serve a single request. A health check that
can't fail isn't a health check.
"""
import logging

from fastapi import APIRouter, Response, status

from app.core.db import get_connection
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])
logger = logging.getLogger("health")


def _database_status() -> tuple[bool, str]:
    """(reachable, detail). Runs the cheapest possible round-trip - the point
    is to prove the connection works, not to measure the database."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True, "ok"
    except Exception as e:
        # Deliberately broad: a health check must report ANY failure rather
        # than propagate it. The class name alone identifies the problem
        # without leaking a connection string into the response body.
        logger.warning("health check: database unreachable (%s)", type(e).__name__)
        return False, f"unreachable ({type(e).__name__})"


@router.get("/health", response_model=HealthResponse)
def get_health(response: Response) -> HealthResponse:
    healthy, database = _database_status()
    if not healthy:
        # 503 rather than 200-with-a-sad-payload: orchestrators and uptime
        # monitors act on the status code, and most never read the body.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="degraded",
            message="World Genre API is running but a dependency is down",
            database=database,
        )
    return HealthResponse(
        status="ok", message="World Genre API is running", database=database
    )
