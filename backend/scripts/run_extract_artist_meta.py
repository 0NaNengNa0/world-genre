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

# How long a recorded miss stands before the artist is asked about again.
# MusicBrainz gains artists constantly, so "not in MusicBrainz" is true on a
# date rather than forever - but re-asking nightly is what made the backlog
# grow instead of drain. 90 days spreads ~800 misses over ~9 requests a night.
MISS_RECHECK_DAYS = 90

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
    """Artists still needing origin metadata, most-charted-recently first.

    Three clauses, each fixing a separate defect measured on 2026-09-16. The
    design note is claude/enrichment-worklist-design.md in the project docs.

    RECENT CHART PRESENCE, not lifetime. The ordering exists so a truncated run
    resolves the artists that actually carry streams - MusicBrainz's rate limit
    caps how many any single run gets through. Without a date filter it ranked
    by everything that EVER charted, so a name that charted heavily in July and
    not since outranked an artist on today's chart.

    INNER JOIN, not LEFT. `artists` is a dimension: merge_dimension inserts and
    updates, nothing ever deletes, so it grows forever by construction. 390 of
    1,030 unresolved artists had not charted anywhere in three days - dead names
    consuming a rate-limited budget every night. They stay in the table; they
    just stop being worked on. Anything that iterates over a dimension needs a
    reason to stop.

    THE RE-CHECK CLAUSE is what makes recording a miss safe. "Never looked up"
    and "looked up, found nothing" used to share one representation
    (resolved_at IS NULL), so the only way to keep the second re-checkable was
    to never record it - which is exactly the treadmill this fixes. Giving the
    miss its own resolved_by value makes the policy expressible as a predicate:
    a miss is a fact with an expiry date, not a permanent verdict.

    Cost of the re-check: if ~800 artists settle as 'not_found', re-asking each
    every 90 days averages ~9 requests a night against a budget of 250.
    """
    rows = run_query(
        f"""
        WITH recent AS (
            SELECT artist_name, COUNT(*) AS chart_rows
            FROM `{dataset_id()}.chart_entries`
            WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
            GROUP BY artist_name
        )
        SELECT a.artist_name, r.chart_rows
        FROM `{dataset_id()}.artists` a
        JOIN recent r ON r.artist_name = a.artist_name
        WHERE a.resolved_at IS NULL
           OR (
                a.resolved_by = 'not_found'
                AND a.resolved_at
                    < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {MISS_RECHECK_DAYS} DAY)
           )
        ORDER BY r.chart_rows DESC, a.artist_name
        """
    )
    return [r["artist_name"] for r in rows]


def _row(name: str, mbid: str | None, meta: dict, source: str) -> dict:
    """One resolved artist as a warehouse row.

    resolved_at is set even when the source knew nothing about the artist,
    which is what stops every later run retrying the same permanent misses -
    "looked up and found nothing" is a different state from "not looked up".
    Until 2026-09-16 this docstring was the only place that was true: the
    caller skipped misses entirely, so they were re-asked every night forever.

    `source` lands in resolved_by alongside the warehouse join's own values
    ('mb_dump_mbid', 'mb_dump_name'). That makes provenance queryable: how much
    coverage came free from the mirror, how much cost a rate-limited request,
    and how much is genuinely unknowable is a GROUP BY rather than an opinion.
    """
    return {
        "artist_name": name,
        "mbid": mbid,
        "origin_country": meta.get("country"),
        "formed_year": meta.get("formed_year"),
        "resolved_at": _NOW,
        "resolved_by": source,
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
        update_columns=[
            "mbid",
            "origin_country",
            "formed_year",
            "resolved_at",
            "resolved_by",
        ],
    )


def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def resolve_via_musicbrainz(name: str, mbid: str | None) -> tuple[str | None, dict]:
    """(mbid, meta) for one artist. Sleeps between HTTP calls only.

    RAISES requests.RequestException on a transport failure, deliberately.

    It used to catch its own errors and return (None, {}) - the SAME value it
    returns for a genuine miss, when MusicBrainz searched and has no such
    artist. The caller could not tell them apart, assumed every (None, {}) was
    a network blip, and skipped the row. So a permanent miss was never recorded
    and was re-asked every night, forever, out of a 250-request budget. That is
    why the backlog went 1,591 -> 1,602 across two full runs instead of down.

    An overloaded sentinel is a bug the caller cannot recover from, because the
    information was destroyed at the boundary. Exceptions are the channel for
    "could not ask"; a return value is the channel for "asked, and here is the
    answer" - including when the answer is nothing.
    """
    if not mbid:
        mbid = musicbrainz.search_artist(name)
        time.sleep(PACING_SLEEP)
        if not mbid:
            # Asked and answered: MusicBrainz has no artist by this name. A
            # result, not a failure - the caller records it.
            return None, {}

    meta = musicbrainz.get_artist_meta(mbid)
    time.sleep(PACING_SLEEP)
    return mbid, meta


def main() -> None:
    lastfm_mbids = known_mbids()

    pending = pending_artists()

    if not pending:
        logger.info(
            "Nothing to resolve: every recently-charting artist is resolved, "
            "or no country has charted in the last 7 days."
        )
        return

    logger.info("%d recently-charting artists need resolving", len(pending))
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
                rows.append(_row(name, mbid, meta, "wikidata_mbid"))
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
                rows.append(_row(name, None, meta, "wikidata_name"))
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
        misses = 0
        for i, name in enumerate(remaining, 1):
            try:
                mbid, meta = resolve_via_musicbrainz(name, lastfm_mbids.get(name))
            except requests.RequestException as e:
                # Genuinely transient: leave resolved_at NULL so the next run
                # retries rather than recording a false verdict.
                logger.warning("  request failed for %s, will retry: %s", name, e)
                continue
            if mbid is None:
                # Asked and answered. Recording this is the whole point: it is
                # what stops the same unanswerable names consuming the budget
                # every night. MISS_RECHECK_DAYS puts them back on the worklist
                # eventually, so nothing is lost permanently.
                misses += 1
            rows.append(_row(name, mbid, meta, "api" if mbid else "not_found"))
            resolved.add(name)
            if i % 50 == 0:
                logger.info("  %d/%d attempted", i, len(remaining))
        _save(rows)
        logger.info(
            "pass 3 (musicbrainz): %d looked up - %d found, %d recorded as "
            "not_found (re-checked after %d days)",
            len(rows),
            len(rows) - misses,
            misses,
            MISS_RECHECK_DAYS,
        )

    still_pending = len(pending) - len(resolved)
    logger.info(
        "Done. %d resolved this run, %d still unresolved.", len(resolved), still_pending
    )


if __name__ == "__main__":
    main()
