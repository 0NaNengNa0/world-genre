"""Fill in artist origin country and formation year from MusicBrainz.

Populates the `artists` dimension, which powers the domestic-vs-imported
share on the country detail view.

    artists (rows created by run_load from chart entries)
        -> MusicBrainz artist lookup
        -> artists.origin_country / formed_year / resolved_at

**Bounded on purpose.** MusicBrainz allows ~1 request/second, and this
project's charts contain ~2,300 unique artists across 20 countries - most
without a known MBID, needing a name search first (2 calls each). Resolving
every one in a single run measured out at roughly 6 hours at 76 countries,
which is not a pipeline task, it's an outage. So each run resolves at most
MAX_PER_RUN artists and stops. The dimension fills in over successive runs,
and because the pipeline is weekly the whole catalogue is covered without any
individual run taking more than a few minutes.

Artists whose lookup genuinely finds nothing still get `resolved_at` set, so
later runs spend their budget on unseen artists instead of retrying the same
permanent misses forever.

Run from the backend/ directory, after run_load:
    python -m scripts.run_load
    python -m scripts.run_extract_artist_meta
"""
import json
import logging
import time
from datetime import datetime, timezone

import requests

from app.core.config import DATA_DIR
from app.core.db import get_connection
from app.services.extractors import musicbrainz

LASTFM_DIR = DATA_DIR / "raw" / "lastfm"

# One run's budget. At ~1.5s pacing and up to 2 calls per artist this is a
# few minutes - short enough to sit inside a DAG task without dominating it.
MAX_PER_RUN = 250
PACING_SLEEP = 1.5

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("run_extract_artist_meta")


def known_mbids() -> dict[str, str]:
    """{artist_name: mbid} from Last.fm's output.

    Last.fm hands back an mbid with each artist, which skips the name-search
    call entirely - halving the request cost for any artist it covers.
    """
    mbids: dict[str, str] = {}
    for path in sorted(LASTFM_DIR.glob("*.json")):
        for artist in json.loads(path.read_text()).get("artists", []):
            name, mbid = artist.get("name"), artist.get("mbid")
            if name and mbid and name not in mbids:
                mbids[name] = mbid
    return mbids


def pending_artists(cur, limit: int) -> list[str]:
    """Artists never looked up yet, most-charted first.

    Ordering by chart presence means a truncated run resolves the artists
    that actually carry streams, so the domestic-share metric becomes
    meaningful early instead of after full coverage.
    """
    cur.execute(
        """
        SELECT a.artist_name
        FROM artists a
        LEFT JOIN chart_entries c ON c.artist_name = a.artist_name
        WHERE a.resolved_at IS NULL
        GROUP BY a.artist_name
        ORDER BY COUNT(c.*) DESC, a.artist_name
        LIMIT %s
        """,
        (limit,),
    )
    return [r[0] for r in cur.fetchall()]


def resolve(name: str, mbid: str | None) -> tuple[str | None, dict]:
    """(mbid, meta) for one artist. Sleeps between HTTP calls, not around
    cache hits, so pacing tracks actual request volume."""
    if not mbid:
        try:
            mbid = musicbrainz.search_artist(name)
        except requests.RequestException as e:
            logger.warning("  search failed for %s, will retry next run: %s", name, e)
            return None, {}
        time.sleep(PACING_SLEEP)
        if not mbid:
            return None, {}

    try:
        meta = musicbrainz.get_artist_meta(mbid)
    except requests.RequestException as e:
        logger.warning("  lookup failed for %s, will retry next run: %s", name, e)
        return mbid, {}
    time.sleep(PACING_SLEEP)
    return mbid, meta


def main() -> None:
    lastfm_mbids = known_mbids()
    now = datetime.now(timezone.utc)

    with get_connection() as conn:
        with conn.cursor() as cur:
            names = pending_artists(cur, MAX_PER_RUN)
            cur.execute("SELECT COUNT(*) FROM artists WHERE resolved_at IS NULL")
            total_pending = cur.fetchone()[0]

    if not names:
        logger.info("Every artist already resolved - nothing to do.")
        return

    logger.info(
        "%d artists unresolved; this run will attempt %d (budget %d)",
        total_pending,
        len(names),
        MAX_PER_RUN,
    )

    resolved = failed = with_country = 0
    for i, name in enumerate(names, 1):
        mbid, meta = resolve(name, lastfm_mbids.get(name))
        if mbid is None and not meta:
            # A network failure, not a genuine miss - leave resolved_at NULL
            # so the next run tries again rather than recording a false blank.
            failed += 1
            continue

        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE artists
                SET mbid = COALESCE(%s, mbid),
                    origin_country = %s,
                    formed_year = %s,
                    resolved_at = %s
                WHERE artist_name = %s
                """,
                (mbid, meta.get("country"), meta.get("formed_year"), now, name),
            )
        resolved += 1
        if meta.get("country"):
            with_country += 1
        if i % 50 == 0:
            logger.info("  %d/%d attempted", i, len(names))

    logger.info(
        "Done. %d resolved (%d with a country), %d deferred to next run. "
        "%d still unresolved overall.",
        resolved,
        with_country,
        failed,
        max(total_pending - resolved, 0),
    )


if __name__ == "__main__":
    main()
