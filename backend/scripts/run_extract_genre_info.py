"""Fetch a short description for each genre from Last.fm's tag.getInfo.

Populates the `genres` table, which backs the blurb shown when you click a
genre in the UI.

Cheap by construction, and deliberately so:

  * The taxonomy is fixed at ~150 buckets (seeds/genre_buckets.txt), so this
    is bounded no matter how many countries or artists exist - unlike the
    artist extractors, which scale with chart size.
  * Results are cached to disk, so a rerun costs zero API calls.
  * Genres Last.fm has no wiki for are still recorded as resolved, so later
    runs don't re-spend calls rediscovering the same blanks.

That makes the whole step roughly a one-time 150 requests, then free.

Run from the backend/ directory:
    python -m scripts.run_extract_genre_info
"""
import json
import logging
import os
import time

import requests
from dotenv import load_dotenv

from app.core.config import DATA_DIR, GENRE_BUCKETS
from app.core.db import get_connection
from app.services.extractors import lastfm

CACHE_PATH = DATA_DIR / "raw" / "lastfm" / "genres.json"

# Last.fm asks for roughly 5 requests/second; this is well inside that and
# keeps the whole run under a minute.
PACING_SLEEP = 0.25

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("run_extract_genre_info")


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    return json.loads(CACHE_PATH.read_text(encoding="utf-8"))


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    load_dotenv()
    api_key = os.environ.get("LASTFM_API_KEY")
    if not api_key:
        logger.error("LASTFM_API_KEY is not set - skipping genre descriptions")
        return

    cache = _load_cache()
    # "other" is this project's own catch-all, not a real genre - asking
    # Last.fm about it would return something about the English word.
    genres = [g for g in GENRE_BUCKETS if g != "other"]
    pending = [g for g in genres if g not in cache]

    logger.info(
        "%d genres in the taxonomy, %d already cached, %d to fetch",
        len(genres),
        len(genres) - len(pending),
        len(pending),
    )

    fetched = 0
    for i, genre in enumerate(pending, 1):
        try:
            info = lastfm.get_tag_info(api_key, genre)
        except requests.RequestException as e:
            logger.warning("  %s failed, will retry next run: %s", genre, e)
            continue
        # A genuine "no wiki" is cached as null so it isn't retried; a network
        # failure above skips the cache entirely so it is.
        cache[genre] = info
        if info:
            fetched += 1
        time.sleep(PACING_SLEEP)
        if i % 25 == 0:
            logger.info("  %d/%d", i, len(pending))

    _save_cache(cache)

    with get_connection() as conn, conn.cursor() as cur:
        for genre, info in cache.items():
            cur.execute(
                """
                INSERT INTO genres (genre, summary, url, resolved_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (genre) DO UPDATE SET
                    summary = EXCLUDED.summary,
                    url = EXCLUDED.url,
                    resolved_at = EXCLUDED.resolved_at
                """,
                (genre, (info or {}).get("summary"), (info or {}).get("url")),
            )

    with_text = sum(1 for v in cache.values() if v)
    logger.info(
        "Done. %d new descriptions this run; %d of %d genres have text.",
        fetched,
        with_text,
        len(cache),
    )


if __name__ == "__main__":
    main()
