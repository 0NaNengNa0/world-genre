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
from app.services.extractors.kworb import parse_chart_rows

PROCESSED_DIR = DATA_DIR / "processed"
KWORB_DIR = DATA_DIR / "raw" / "kworb"
LASTFM_DIR = DATA_DIR / "raw" / "lastfm"
DEEZER_ARTISTS_PATH = DATA_DIR / "raw" / "deezer" / "artists.json"

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
        "artists", "artist_name", [{"artist_name": n} for n in sorted(names)]
    )
    fans = deezer_fan_rows()
    merge_dimension(
        "artists", "artist_name", fans, update_columns=["deezer_fans"]
    )

    logger.info(
        "Done. %d/%d countries%s. %d chart entries, %d listener rows, "
        "%d artists, %d with Deezer fans.",
        loaded,
        len(COUNTRIES),
        f" ({len(skipped)} skipped)" if skipped else "",
        len(collected["chart_entries"]),
        len(collected["country_artist_listeners"]),
        len(names),
        len(fans),
    )


if __name__ == "__main__":
    main()
