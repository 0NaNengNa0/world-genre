from pydantic import BaseModel


class HealthResponse(BaseModel):
    # "ok" only when every dependency the API needs is actually reachable,
    # not merely when the process is running.
    status: str
    # What the check found, so a failure says WHAT is unavailable rather than
    # just that something is. Never carries a path, bucket name or
    # credential - this endpoint is public.
    data: str
