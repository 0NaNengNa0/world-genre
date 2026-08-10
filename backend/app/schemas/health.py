from pydantic import BaseModel


class HealthResponse(BaseModel):
    # "ok" only when every dependency the API needs is actually reachable.
    status: str
    message: str
    # Per-dependency detail, so a failing check says WHAT is down rather than
    # just that something is.
    database: str
