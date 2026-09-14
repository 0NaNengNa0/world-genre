"""Load cleansed data into BigQuery - the "load" stage of extract -> cleanse
-> load.

Reads data/processed/{code}.json (from run_cleanse.py) plus the raw kworb,
Last.fm and Deezer files, and writes the warehouse tables defined in
sql/bigquery/schema.sql.

    python -m scripts.run_init_bq     # idempotent, safe every run
    python -m scripts.run_cleanse
    python -m scripts.run_load

Two structural differences from the Postgres version this replaces, both
forced by BigQuery rather than chosen:

**Rows are accumulated, then written once per table.** Postgres took a row per
INSERT and that was fine; BigQuery bills and rate-limits INSERT DML, and a
load job has fixed overhead regardless of size. Writing 7,000 chart entries as
one load job instead of 7,000 statements is the difference between seconds and
an afternoon. So every country is collected in memory first - trivial at this
scale, ~7k rows - and flushed per table at the end.

**Idempotency is constructed, not declared.** There is no ON CONFLICT, and
BigQuery does not enforce the primary keys the schema declares. Facts are
rewritten a whole partition at a time; dimensions are merged so that columns
owned by the enrichment scripts survive. See app/core/bq_load.py.
"""
import json
import logging
from datetime import date, datetime, timezone

from app.core.bq_load import merge_dimension, replace_partition
from app.core.config import COUNTRIES, DATA_DIR
from app.services.cleansing import match_key
from app.services.extractors.kworb import parse_chart_rows

PROCESSED_DIR = DATA_DIR / "processed"
KWORB_DIR = DATA_DIR / "raw" / "kworb"
LASTFM_DIR = DATA_DIR / "raw" / "lastfm"
DEEZER_ARTISTS_PATH = DATA_DIR / "raw" / "deezer" / "artists.json"
QUALITY_REPORT_PATH = PROCESSED_DIR / "_quality_report.json"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("run_load")


def _load_processed(code: str) -> dict | None:
    path = PROCESSED_DIR / f"{code}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _iso(value: date) -> str:
    """BigQuery's JSON load path takes DATE as an ISO string, not a date."""
    return value.isoformat()


def country_rows(code: str, name: str, record: dict, snapshot_date: date) -> dict:
    """Every row one country contributes, keyed by table.

    Pure: takes a cleansed record, returns dicts. No client, no network, so
    the row-shaping logic is unit-testable without BigQuery - which matters
    more here than it did with Postgres, because there is no local BigQuery to
    test against.
    """
    day = _iso(snapshot_date)
    rows: dict[str, list[dict]] = {
        "countries": [{"code": code, "name": name}],
        "country_snapshots": [
            {
                "country_code": code,
                "snapshot_date": day,
                "artist_count": record.get("artist_count", 0),
            }
        ],
        "country_genre_scores": [],
        "country_artist_genres": [],
        "country_top_artists": [],
        "genres": [],
    }

    # run_cleanse writes one `genres` list carrying both scores per genre, so
    # this is a straight append - no merging of separate popularity and
    # distinctiveness lists, which previously risked one overwriting the
    # other's columns with defaults.
    for row in record.get("genres", []):
        rows["country_genre_scores"].append(
            {
                "country_code": code,
                "genre": row["genre"],
                "score": row["score"],
                "distinctiveness": row.get("distinctiveness", 0.0),
                "sources": list(row["sources"]),
                "snapshot_date": day,
            }
        )

    for genre, artists in record.get("artists_by_genre", {}).items():
        for artist_name in artists:
            rows["country_artist_genres"].append(
                {
                    "country_code": code,
                    "genre": genre,
                    "artist_name": artist_name,
                    "snapshot_date": day,
                }
            )
        # Every genre seen becomes a reference row, so run_extract_genre_info
        # has a worklist even before descriptions exist, and a join against
        # `genres` never drops a genre.
        rows["genres"].append({"genre": genre})

    for rank, artist_name in enumerate(record.get("top_artists", []), start=1):
        rows["country_top_artists"].append(
            {
                "country_code": code,
                "artist_name": artist_name,
                "rank": rank,
                "snapshot_date": day,
            }
        )

    return rows


def chart_rows(code: str, snapshot_date: date) -> list[dict]:
    """One country's chart, straight from data/raw/kworb.

    Read from raw rather than processed because these are measured facts and
    cleansing has nothing to add to a stream count. It also means the fact
    table can be rebuilt from files already on disk, with no re-scrape.
    """
    path = KWORB_DIR / f"{code}.json"
    if not path.exists():
        return []

    day = _iso(snapshot_date)
    return [
        {
            "country_code": code,
            "snapshot_date": day,
            "position": e["position"],
            "artist_name": e["artist"],
            "track_name": e["track"],
            "days_on_chart": e["days_on_chart"],
            "peak_position": e["peak_position"],
            "daily_streams": e["daily_streams"],
            "weekly_streams": e["weekly_streams"],
            "total_streams": e["total_streams"],
        }
        for e in parse_chart_rows(json.loads(path.read_text()).get("rows", []))
    ]


def listener_rows(code: str, snapshot_date: date) -> list[dict]:
    """One country's Last.fm listener counts, from data/raw/lastfm.

    Per-country, unlike Deezer fans, which is what makes it directly
    comparable with the Spotify streams in chart_entries rather than a
    different unit bolted alongside.
    """
    path = LASTFM_DIR / f"{code}.json"
    if not path.exists():
        return []

    day = _iso(snapshot_date)
    rows = []
    for artist in json.loads(path.read_text()).get("artists", []):
        name, listeners = artist.get("name"), artist.get("listeners")
        if not name:
            continue
        try:
            count = int(listeners) if listeners is not None else None
        except (TypeError, ValueError):
            count = None
        rows.append(
            {
                "country_code": code,
                "artist_name": name,
                "listeners": count,
                "snapshot_date": day,
            }
        )
    return rows


def deezer_fan_rows() -> list[dict]:
    """Deezer fan counts for the artists dimension.

    `nb_fan` arrives on the same lookup that fetches images, so this costs
    nothing extra - it was simply being dropped when artists.json was written.
    """
    if not DEEZER_ARTISTS_PATH.exists():
        return []

    payload = json.loads(DEEZER_ARTISTS_PATH.read_text())
    return [
        {"artist_name": name, "deezer_fans": (data or {}).get("nb_fan")}
        for name, data in payload.items()
        if (data or {}).get("nb_fan") is not None
    ]


def quality_rows(countries: dict, snapshot_date: date) -> list[dict]:
    """Per-country cleanse statistics, from run_cleanse's quality report.

    Pure, like country_rows: takes the report's `countries` mapping, returns
    row dicts. No file, no client.

    This exists because the most useful data-quality metric in the pipeline
    was being computed and then thrown away. cleansing.merge_genre_signals
    counts how many raw tags it could not classify - tags that normalized to
    nothing, or fell through genre_buckets.bucket_genre to the "other" bucket
    - and run_cleanse writes those counts to a local JSON file. But `other` is
    dropped BEFORE scoring, so it never reaches country_genre_scores, and no
    query against the warehouse can see the number at all. A data-quality gate
    could not assert on the pipeline's own headline quality metric.

    Loading it here fixes three things at once: the rate becomes gateable (see
    the unclassified_tag_rate check), it gains history so a threshold can be
    set from a range rather than a guess, and it stops being a file that the
    next cleanse run overwrites. The report keeps only "latest" - there is no
    history on disk to backfill from, which is exactly the cost of leaving a
    metric outside the warehouse.
    """
    day = _iso(snapshot_date)
    rows = []
    for code, stats in sorted(countries.items()):
        total = stats.get("total_genre_tags", 0) or 0
        unclassified = stats.get("unclassified_genre_tags", 0) or 0
        rows.append(
            {
                "country_code": code,
                "snapshot_date": day,
                "artist_count": stats.get("artist_count", 0),
                "total_genre_tags": total,
                "unclassified_genre_tags": unclassified,
                # Recomputed rather than copied from the report. The report's
                # value is rounded to 4dp for human reading, and a rate stored
                # beside its own numerator and denominator should agree with
                # them exactly - otherwise a later query gets two answers.
                "unclassified_rate": (unclassified / total) if total else None,
                "distinct_genres": stats.get("distinct_genres", 0),
            }
        )
    return rows


def _load_quality_report() -> dict:
    """The `countries` section of run_cleanse's report, or empty if absent.

    Empty rather than an error: run_load has always been runnable against raw
    files alone, and a missing report should cost the quality check its
    numbers (it abstains) rather than the whole load.
    """
    if not QUALITY_REPORT_PATH.exists():
        return {}
    return json.loads(QUALITY_REPORT_PATH.read_text()).get("countries", {})


def lastfm_mbid_rows() -> list[dict]:
    """MusicBrainz ids that Last.fm already handed us, keyed by artist name.

    `geo.gettopartists` returns an mbid alongside the name and listener count,
    and until now this loader kept the first two and dropped the third. The
    only way an mbid ever reached the warehouse was the rate-limited search in
    run_extract_artist_meta, which resolves on the order of fifty artists a
    night - while the identifier was arriving free, daily, for every Last.fm
    artist and being discarded on the way in.

    Merged with `fill_columns`, never `update_columns`: Last.fm's id is
    convenient, the enrichment stage's is deliberate, and a free value must
    never overwrite a verified one.
    """
    rows = []
    for country in COUNTRIES:
        path = LASTFM_DIR / f"{country['kworb_code']}.json"
        if not path.exists():
            continue
        for artist in json.loads(path.read_text()).get("artists", []):
            name, mbid = artist.get("name"), artist.get("mbid")
            # Last.fm returns an empty string rather than null for artists it
            # has no id for, which is why this tests truthiness, not None.
            if name and mbid:
                rows.append({"artist_name": name, "mbid": mbid})
    return _dedupe(rows, ("artist_name",))


def artist_rows(names: set[str]) -> list[dict]:
    """The artists dimension's own rows: the name, and its join key.

    `match_name` is computed here, at load time, because cleansing.match_key
    cannot be reproduced in SQL - it drops trailing collaborators. Both sides
    of the MusicBrainz join have to be normalised by identical Python or the
    join misses precisely the rows the normaliser was written for.

    It is in `update_columns` rather than `fill_columns` because it is derived
    purely from artist_name: recomputing it every run is free. Note this covers
    only the names on TODAY's charts - backfill_match_names below is what keeps
    the rest of the dimension in step.
    """
    return [{"artist_name": name, "match_name": match_key(name)} for name in sorted(names)]


def backfill_match_names() -> list[dict]:
    """`match_name` for every artist in the dimension, not just today's chart.

    WHY THIS EXISTS. artist_rows above only sees the ~2,900 names on today's
    charts, while `artists` holds every name ever charted - 4,356 of them. The
    first real run of the MusicBrainz join found 742 of 1,307 unresolved
    artists carrying no match_name at all: 57% of the backlog was ineligible
    for name matching, not because the mirror lacked those artists but because
    nobody had computed their key. An artist who charted last month and hasn't
    since would have waited for a chance re-entry to become resolvable.

    That is the general shape of the bug, and it is worth recognising away from
    this codebase: a derived column maintained only on the rows a job happens
    to touch drifts out of step with the rows it does not, and the drift hides
    because the column IS populated - just not everywhere. Backfilling makes
    the column a function of the table rather than of the day's input.

    RECOMPUTED, not filled-where-null, deliberately. Filling only nulls would
    be cheaper and would still have fixed the 742, but it would freeze every
    existing key at whatever match_key produced when that row was first seen.
    The normaliser is still changing; recomputing is what makes an improvement
    to it heal the whole table on the next run.

    The cost is one read of the dimension per run - trivial at 4k rows, and it
    scales with the dimension rather than with the day's chart. At a few
    million artists this would want to be an incremental UPDATE, which is only
    possible if the key is expressible in SQL. match_key is not, so the honest
    answer at that scale is a UDF or a separate scheduled recompute, not a
    nightly full pass.
    """
    from app.core.bq import dataset_id, run_query

    rows = run_query(f"SELECT artist_name FROM `{dataset_id()}.artists`")
    return [
        {"artist_name": row["artist_name"], "match_name": match_key(row["artist_name"])}
        for row in rows
        if row["artist_name"]
    ]


def _dedupe(rows: list[dict], key: tuple[str, ...]) -> list[dict]:
    """Last write wins on a repeated key.

    Necessary because BigQuery will not do it for us. Postgres rejected a
    duplicate primary key outright; here a duplicate simply becomes two rows,
    and every downstream count silently doubles. The realistic source of one
    is the same artist appearing under two genres, or a country appearing in
    the seed file twice.
    """
    seen: dict[tuple, dict] = {}
    for row in rows:
        seen[tuple(row[k] for k in key)] = row
    return list(seen.values())


def main() -> None:
    today = datetime.now(timezone.utc).date()
    collected: dict[str, list[dict]] = {
        "countries": [],
        "country_snapshots": [],
        "country_genre_scores": [],
        "country_artist_genres": [],
        "country_top_artists": [],
        "genres": [],
        "chart_entries": [],
        "country_artist_listeners": [],
        "cleanse_quality": [],
    }
    loaded, skipped = 0, []

    for country in COUNTRIES:
        code, name = country["kworb_code"], country["country_name"]
        record = _load_processed(code)
        if record is None:
            skipped.append(code)
            logger.warning("%s: no processed data (run run_cleanse first)", code)
            continue

        for table, rows in country_rows(code, name, record, today).items():
            collected[table].extend(rows)

        # Chart facts are loaded from the raw kworb file, independent of
        # whether cleansing produced anything for that country.
        entries = chart_rows(code, today)
        collected["chart_entries"].extend(entries)
        collected["country_artist_listeners"].extend(listener_rows(code, today))
        loaded += 1
        logger.info("%s: collected (%d chart entries)", code, len(entries))

    # Every charting or scrobbled artist becomes a dimension row before any
    # enrichment has run, so run_extract_artist_meta has a worklist and joins
    # against `artists` never silently drop rows.
    names = {r["artist_name"] for r in collected["chart_entries"]}
    names |= {r["artist_name"] for r in collected["country_artist_listeners"]}

    # Whole-run rather than per-country, because run_cleanse writes one report
    # for the whole pass. Partitioned and replaced like any other fact, so a
    # rerun of the same day overwrites rather than doubling the tag counts.
    collected["cleanse_quality"] = quality_rows(_load_quality_report(), today)

    facts = {
        "country_snapshots": ("country_code", "snapshot_date"),
        "country_genre_scores": ("country_code", "genre", "snapshot_date"),
        "country_artist_genres": (
            "country_code",
            "genre",
            "artist_name",
            "snapshot_date",
        ),
        "country_top_artists": ("country_code", "snapshot_date", "rank"),
        "chart_entries": ("country_code", "snapshot_date", "position"),
        "country_artist_listeners": (
            "country_code",
            "artist_name",
            "snapshot_date",
        ),
        "cleanse_quality": ("country_code", "snapshot_date"),
    }
    for table, key in facts.items():
        rows = _dedupe(collected[table], key)
        written = replace_partition(table, today, rows)
        logger.info("%s: %d rows", table, written)

    # Dimensions are merged, never truncated. `artists` and `genres` carry
    # columns filled in by the enrichment scripts across earlier runs -
    # origin_country, deezer_fans, summary - and a truncate here would look
    # like a clean run while silently discarding hours of rate-limited work.
    merge_dimension(
        "countries",
        "code",
        _dedupe(collected["countries"], ("code",)),
        update_columns=["name"],
    )
    merge_dimension(
        "genres", "genre", _dedupe(collected["genres"], ("genre",))
    )
    merge_dimension(
        "artists", "artist_name", artist_rows(names), update_columns=["match_name"]
    )
    # AFTER the merge above, not before: that one inserts today's new artists,
    # and reading the dimension first would miss them for a whole day.
    backfilled = backfill_match_names()
    merge_dimension(
        "artists", "artist_name", backfilled, update_columns=["match_name"]
    )
    logger.info("match_name recomputed for %d artists in the dimension.", len(backfilled))
    # Free MusicBrainz ids, filled only where the dimension has none. See
    # lastfm_mbid_rows for why this is fill and not update.
    mbids = lastfm_mbid_rows()
    merge_dimension("artists", "artist_name", mbids, fill_columns=["mbid"])

    fans = deezer_fan_rows()
    merge_dimension(
        "artists", "artist_name", fans, update_columns=["deezer_fans"]
    )

    logger.info(
        "Done. %d/%d countries%s. %d chart entries, %d listener rows, "
        "%d artists, %d with Last.fm mbids, %d with Deezer fans, %d quality rows.",
        loaded,
        len(COUNTRIES),
        f" ({len(skipped)} skipped)" if skipped else "",
        len(collected["chart_entries"]),
        len(collected["country_artist_listeners"]),
        len(names),
        len(mbids),
        len(fans),
        len(collected["cleanse_quality"]),
    )
    if not collected["cleanse_quality"]:
        # Not fatal, but the unclassified_tag_rate check will abstain without
        # it, and a check that abstains every run is indistinguishable from a
        # check nobody wrote.
        logger.warning(
            "No quality report at %s - run run_cleanse to produce one.",
            QUALITY_REPORT_PATH,
        )


if __name__ == "__main__":
    main()
