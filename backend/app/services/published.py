"""Serve API responses from the JSON that scripts/run_publish.py wrote.

This module replaced a 641-line psycopg2 service, and the request path now
contains no database at all - no pool, no cursor, no SQL. Every query the API
used to issue per request now runs once per pipeline run, in the publish step.

Two things this has to get right:

**Caching, without serving yesterday's data forever.** The files are read once
and held in memory, because parsing them per request would be the same
per-request cost the publish step exists to remove. But the pipeline rewrites
them daily and nothing restarts the API, so a cache with no expiry would serve
whatever existed at boot - indefinitely, which is exactly the bug the artist
image mapping had. Hence a TTL.

**Not stat-ing a bucket per request.** Freshness is checked at most once per
TTL window rather than on every call. Against GCS each check is an HTTP round
trip, and the underlying data changes once a day.
"""
import json
import logging
import os
import threading
import time

from app.core.config import DATA_DIR
from app.core.storage import resolve_data_dir

logger = logging.getLogger(__name__)

# Long enough that a busy API isn't re-reading constantly, short enough that a
# pipeline run is picked up without a deploy. The data changes daily, so
# anything under an hour is generous.
CACHE_TTL_SECONDS = 300.0

_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def published_dir():
    return resolve_data_dir(
        os.environ.get("PUBLISH_DIR"), DATA_DIR / "published"
    )


def _read(relative: str):
    """Parsed JSON for one published file, or None if it isn't there.

    A missing file is None rather than an exception because it is a real and
    recoverable state: the API can be deployed before the first publish has
    run. Routes turn that into a clear 503 instead of a stack trace.
    """
    now = time.monotonic()
    with _lock:
        entry = _cache.get(relative)
        if entry and now - entry[0] < CACHE_TTL_SECONDS:
            return entry[1]

    path = published_dir() / relative
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # Covers a missing local file, a missing GCS object and a denied
        # request alike - cloudpathlib raises its own types, not OSError.
        logger.warning("published file unavailable: %s", relative)
        payload = None

    with _lock:
        _cache[relative] = (now, payload)
    return payload


def reset_cache() -> None:
    """Drop everything cached, so the next read hits storage."""
    with _lock:
        _cache.clear()


def get_country_summaries() -> list[dict] | None:
    payload = _read("countries.json")
    return payload["countries"] if payload else None


def get_country_detail(code: str) -> dict | None:
    return _read(f"country/{code}.json")


def get_genre_detail(code: str, genre: str) -> dict | None:
    """One genre panel, read out of the country payload it was published in.

    Nested rather than fetched from its own file: a genre panel is only ever
    opened from a country that has just been loaded, so this is a dictionary
    lookup on data already in memory rather than another round trip.
    """
    detail = get_country_detail(code)
    if not detail:
        return None
    return detail.get("genre_details", {}).get(genre)


def get_global_artists() -> list[dict] | None:
    payload = _read("artists-global.json")
    return payload["artists"] if payload else None


def get_trending_genres() -> list[dict] | None:
    payload = _read("genres-trending.json")
    return payload["genres"] if payload else None


def is_available() -> bool:
    """Whether a publish has landed - what /api/health now reports on."""
    return _read("countries.json") is not None
