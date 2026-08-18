"""Fill in artist origin country and formation year.

Populates the `artists` dimension, which powers the domestic-vs-imported
share on the country detail view.

**Three passes, cheapest first.** The naive version asked MusicBrainz about
every artist individually, and MusicBrainz allows ~1 request/second: measured
on this dataset that was 4,244 calls, roughly 106 minutes of pure waiting,
spread over ten weekly runs before coverage was complete.

    1. Wikidata by MusicBrainz id   - hundreds per query, exact match
    2. Wikidata by artist name      - hundreds per query, unambiguous only
    3. MusicBrainz, bounded         - whatever's left, at 1 req/sec

Wikidata answers the same question (P495/P27 for country, P571 for
formation) but its SPARQL endpoint takes a VALUES block, so one request
covers a whole batch instead of one artist. Passes 1 and 2 finish in
seconds; pass 3 exists because Wikidata's coverage of smaller chart artists
is patchy and MusicBrainz's name search is better at finding them.

Artists resolved by any pass get `resolved_at` set, so later runs spend their
MusicBrainz budget only on genuinely unknown names rather than rediscovering
the same blanks.

Run from the backend/ directory, after run_load:
    python -m scripts.run_load
    python -m scripts.run_extract_artist_meta
"""
import json
import logging
import time
from datetime import datetime, timezone

import requests

from app.core.bq import dataset_id, run_query
from app.core.bq_load import merge_dimension
from app.core.config import DATA_DIR
from app.services.extractors import musicbrainz, wikidata

LASTFM_DIR = DATA_DIR / "raw" / "lastfm"

# Wikidata batches. Large enough that the whole catalogue is a handful of
# requests, small enough to stay inside the endpoint's 60-second timeout.
WIKIDATA_BATCH = 200
PAUSE_BETWEEN_BATCHES = 1.0

# MusicBrainz fallback budget per run. Only reached for artists both Wikidata
# passes missed, which is a far smaller set than before.
MAX_MUSICBRAINZ_PER_RUN = 250
PACING_SLEEP = 1.5

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("run_extract_artist_meta")

# One timestamp for the whole run, ISO-formatted because rows reach
# BigQuery through a JSON load rather than a typed driver.
_NOW = datetime.now(timezone.utc).isoformat()


def known_mbids() -> dict[str, str]:
    """{artist_name: mbid} from Last.fm's output, which returns one per
    artist - skipping a lookup entirely for anyone it covers."""
    mbids: dict[str, str] = {}
    for path in sorted(LASTFM_DIR.glob("*.json")):
        for artist in json.loads(path.read_text()).get("artists", []):
            name, mbid = artist.get("name"), artist.get("mbid")
            if name and mbid and name not in mbids:
                mbids[name] = mbid
    return mbids


def pending_artists() -> list[str]:
    """Unresolved artists, most-charted first.

    Ordering by chart presence means a truncated run resolves the artists that
    actually carry streams, so domestic share becomes meaningful early rather
    than only at full coverage. That matters because MusicBrainz's rate limit
    caps how many any single run can get through.

    COUNT(c.artist_name) rather than Postgres's COUNT(c.*): BigQuery has no
    row-wildcard count, and counting a column from the outer-joined side
    correctly yields 0 for artists that never charted, which is the behaviour
    the ordering depends on.
    """
    rows = run_query(
        f"""
        SELECT a.artist_name, COUNT(c.artist_name) AS chart_rows
        FROM `{dataset_id()}.artists` a
        LEFT JOIN `{dataset_id()}.chart_entries` c
          ON c.artist_name = a.artist_name
        WHERE a.resolved_at IS NULL
        GROUP BY a.artist_name
        ORDER BY chart_rows DESC, a.artist_name
        """
    )
    return [r["artist_name"] for r in rows]


def _row(name: str, mbid: str | None, meta: dict) -> dict:
    """One resolved artist as a warehouse row.

    resolved_at is set even when the source knew nothing about the artist,
    which is what stops every later run retrying the same permanent misses -
    "looked up and found nothing" is a different state from "not looked up".
    """
    return {
        "artist_name": name,
        "mbid": mbid,
        "origin_country": meta.get("country"),
        "formed_year": meta.get("formed_year"),
        "resolved_at": _NOW,
    }


def _save(rows: list[dict]) -> None:
    """Write one batch of resolved artists.

    update_columns is explicit and deliberately excludes deezer_fans: that
    column belongs to run_load, and a blanket update here would blank it on
    every enrichment run. The dimension is written by several scripts that
    each own different columns.
    """
    if not rows:
        return
    merge_dimension(
        "artists",
        "artist_name",
        rows,
        update_columns=["mbid", "origin_country", "formed_year", "resolved_at"],
    )


def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def resolve_via_musicbrainz(name: str, mbid: str | None) -> tuple[str | None, dict]:
    """(mbid, meta) for one artist. Sleeps between HTTP calls only."""
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

    pending = pending_artists()

    if not pending:
        logger.info("Every artist already resolved - nothing to do.")
        return

    logger.info("%d artists unresolved", len(pending))
    resolved: set[str] = set()

    # --- Pass 1: Wikidata by MusicBrainz id (exact) ---
    with_mbid = {n: lastfm_mbids[n] for n in pending if n in lastfm_mbids}
    if with_mbid:
        by_mbid = {mbid: name for name, mbid in with_mbid.items()}
        rows = []
        for batch in _chunks(list(by_mbid), WIKIDATA_BATCH):
            try:
                found = wikidata.fetch_meta_by_mbids(batch)
            except requests.RequestException as e:
                logger.warning("  wikidata mbid batch failed, skipping: %s", e)
                continue
            for mbid, meta in found.items():
                name = by_mbid[mbid]
                rows.append(_row(name, mbid, meta))
                resolved.add(name)
            time.sleep(PAUSE_BETWEEN_BATCHES)
        _save(rows)
        logger.info(
            "pass 1 (wikidata by mbid): %d/%d resolved in %d queries",
            len(rows),
            len(with_mbid),
            -(-len(with_mbid) // WIKIDATA_BATCH),
        )

    # --- Pass 2: Wikidata by name (ambiguous labels rejected) ---
    remaining = [n for n in pending if n not in resolved]
    if remaining:
        rows = []
        for batch in _chunks(remaining, WIKIDATA_BATCH):
            try:
                found = wikidata.fetch_meta_by_names(batch)
            except requests.RequestException as e:
                logger.warning("  wikidata name batch failed, skipping: %s", e)
                continue
            for name, meta in found.items():
                rows.append(_row(name, None, meta))
                resolved.add(name)
            time.sleep(PAUSE_BETWEEN_BATCHES)
        _save(rows)
        logger.info(
            "pass 2 (wikidata by name): %d/%d resolved in %d queries",
            len(rows),
            len(remaining),
            -(-len(remaining) // WIKIDATA_BATCH),
        )

    # --- Pass 3: MusicBrainz, bounded ---
    remaining = [n for n in pending if n not in resolved][:MAX_MUSICBRAINZ_PER_RUN]
    if remaining:
        logger.info(
            "pass 3 (musicbrainz): attempting %d at ~1 req/sec (budget %d)",
            len(remaining),
            MAX_MUSICBRAINZ_PER_RUN,
        )
        rows = []
        for i, name in enumerate(remaining, 1):
            mbid, meta = resolve_via_musicbrainz(name, lastfm_mbids.get(name))
            if mbid is None and not meta:
                # Network failure rather than a genuine miss - leave
                # resolved_at NULL so the next run retries instead of
                # recording a false blank.
                continue
            rows.append(_row(name, mbid, meta))
            resolved.add(name)
            if i % 50 == 0:
                logger.info("  %d/%d attempted", i, len(remaining))
        _save(rows)
        logger.info("pass 3 (musicbrainz): %d resolved", len(rows))

    still_pending = len(pending) - len(resolved)
    logger.info(
        "Done. %d resolved this run, %d still unresolved.", len(resolved), still_pending
    )


if __name__ == "__main__":
    main()
