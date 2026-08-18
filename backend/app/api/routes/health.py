"""Liveness plus a real readiness signal.

Reports on whether published data is actually readable, not merely that the
process is up. A container that starts fine but can't reach its serving bucket
- wrong PUBLISH_DIR, missing IAM binding, publish never run - is exactly the
failure worth catching here, and it is invisible to a plain 200.
"""
from fastapi import APIRouter, Response

from app.schemas.health import HealthResponse
from app.services import published

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    if published.is_available():
        return HealthResponse(status="ok", data="published data readable")

    # 503 rather than 200-with-a-warning, so a load balancer or uptime check
    # treats it as unhealthy without needing to parse the body.
    response.status_code = 503
    return HealthResponse(status="degraded", data="published data unavailable")
