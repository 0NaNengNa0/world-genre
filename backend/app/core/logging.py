"""API logging setup.

The pipeline scripts already log; the API didn't at all, so a failing request
left nothing behind but whatever uvicorn happened to print. This keeps the
format identical to the scripts' so a single `docker compose logs` reads
consistently.

LOG_LEVEL overrides the default (INFO) without a code change - useful for
turning on DEBUG against a misbehaving deployment.
"""
import logging
import os

LOG_FORMAT = "%(levelname)s %(name)s %(message)s"


def configure_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format=LOG_FORMAT,
        # uvicorn installs its own handlers first, so basicConfig would
        # otherwise be a no-op and nothing here would ever appear.
        force=True,
    )
