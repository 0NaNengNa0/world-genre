"""Publish API payloads as JSON - the stage that keeps requests off BigQuery.

    python -m scripts.run_publish

BigQuery answers a query in roughly 0.5-2s regardless of table size, because
it is an analytical engine with no point lookups. A country detail page needs
about six queries, so serving requests from it directly would mean
multi-second clicks and a scan charge on every one.

The data changes once a day. So every read runs once here, at pipeline time,
and the results are written as static JSON to PUBLISH_DIR - a GCS bucket in
the cloud, a local directory otherwise. The API then serves files. Response
time drops to about 20ms, cost to nothing, and the request path needs no
database at all.

The layout is deliberately few, larger files rather than many small ones:

    countries.json              every country summary (the grid and map)
    country/{code}.json         one country's full detail, genre panels included
    artists-global.json         worldwide artist ranking
    genres-trending.json        biggest genre movements

Genre detail is nested inside the country file rather than written as ~900
separate objects. It is small, it is always fetched in the context of a
country that has just been fetched anyway, and a per-genre object would mean a
publish step that scales with genres times countries.
"""
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.core.bq import dataset_id, run_query
from app.core.config import DATA_DIR, SQL_DIR
from app.services.images import cover_image

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("run_publish")

QUERY_DIR = SQL_DIR / "bigquery" / "queries"

# Weighting modes for the genre donut. Restricted to a hardcoded allowlist
# because the value is substituted into SQL as an identifier, which no bind
# parameter can carry - it must never take caller-supplied text.
_WEIGHT_COLUMNS = {"popularity": "score", "distinctiveness": "distinctiveness"}

GENRE_LIMIT = 12
ARTIST_LIMIT = 5
TRACK_LIMIT = 10
GEM_LIMIT = 10
GLOBAL_ARTIST_LIMIT = 40
GENRE_ARTIST_LIMIT = 12
POPULARITY_LIMIT = 15

# Countries published concurrently. The work is API latency, not local CPU.
MAX_WORKERS = 8


def publish_dir():
    """Where payloads are written: PUBLISH_DIR, or data/published locally."""
    from app.core.storage import resolve_data_dir

    return resolve_data_dir(
        os.environ.get("PUBLISH_DIR"), DATA_DIR / "published"
    )


def query(name: str, params: dict | None = None, **substitutions) -> list[dict]:
    """Run a file from sql/bigquery/queries with the dataset filled in."""
    sql = (QUERY_DIR / f"{name}.sql").read_text(encoding="utf-8")
    sql = sql.replace("{dataset}", dataset_id())
    for key, value in substitutions.items():
        sql = sql.replace("{" + key + "}", value)
    return run_query(sql, params)


def _genre_breakdown(code: str, mode: str) -> dict:
    """One weighting mode's donut, with the leftover slice made explicit.

    The percentages come back computed over every qualifying genre, not just
    the returned top N, so the shown slices plus `other_percentage` add to
    100. Deriving `other` here rather than in the browser keeps the invariant
    with the query that guarantees it.
    """
    rows = query(
        "country_genre_shares",
        {"code": code, "limit": GENRE_LIMIT},
        weight_column=_WEIGHT_COLUMNS[mode],
    )
    shown = sum(r["percentage"] or 0 for r in rows)
    total_genres = rows[0]["genre_count"] if rows else 0

    return {
        "genres": [
            {
                "genre": r["genre"],
                "score": r["score"],
                "distinctiveness": r["distinctiveness"],
                "sources": list(r["sources"] or []),
                "percentage": float(r["percentage"] or 0),
            }
            for r in rows
        ],
        # Clamped at 0: floating-point addition of the shown slices can
        # exceed 100 by a hair, and a negative "other" slice would render as
        # a wedge pointing the wrong way.
        "other_percentage": max(0.0, round(100.0 - shown, 2)),
        "other_genre_count": max(0, total_genres - len(rows)),
    }


def _domestic_share(row: dict | None) -> dict | None:
    """Shared shaping for the single-country and all-countries variants.

    `coverage_percentage` travels with the figure deliberately. Domestic share
    is uninterpretable without it: 40 percent domestic means nothing if only
    a tenth of the country's streams have a known artist origin.
    """
    if not row or not row.get("total_entries"):
        return None
    return {
        "domestic_percentage": float(row["domestic_percentage"] or 0),
        "coverage_percentage": float(row["coverage_percentage"] or 0),
        "classified_entries": int(row["classified_entries"] or 0),
        "total_entries": int(row["total_entries"] or 0),
    }


def build_summaries() -> list[dict]:
    """Every country's headline figures, in one pass rather than per country.

    QUALIFY replaces the Postgres LEFT JOIN LATERAL that picked each country's
    latest snapshot - BigQuery has no LATERAL, and QUALIFY expresses
    "top row per partition" more directly than the subquery either dialect
    would otherwise need.
    """
    countries = run_query(
        f"""
        SELECT c.code, c.name, COALESCE(s.artist_count, 0) AS artist_count
        FROM `{dataset_id()}.countries` c
        LEFT JOIN (
            SELECT country_code, artist_count
            FROM `{dataset_id()}.country_snapshots`
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY country_code ORDER BY snapshot_date DESC
            ) = 1
        ) s ON s.country_code = c.code
        ORDER BY c.name
        """
    )

    genres = run_query(
        f"""
        WITH latest AS (
            SELECT country_code, MAX(snapshot_date) AS snapshot_date
            FROM `{dataset_id()}.country_genre_scores`
            GROUP BY country_code
        ),
        ranked AS (
            SELECT
                g.country_code,
                g.genre,
                g.distinctiveness,
                ROW_NUMBER() OVER (
                    PARTITION BY g.country_code ORDER BY g.score DESC
                ) AS popularity_rank,
                ROW_NUMBER() OVER (
                    PARTITION BY g.country_code ORDER BY g.distinctiveness DESC
                ) AS distinctiveness_rank
            FROM `{dataset_id()}.country_genre_scores` g
            JOIN latest l
              ON l.country_code = g.country_code
             AND l.snapshot_date = g.snapshot_date
        )
        SELECT country_code, genre, distinctiveness,
               popularity_rank, distinctiveness_rank
        FROM ranked
        WHERE popularity_rank <= 3 OR distinctiveness_rank <= 3
        """
    )

    artists = run_query(
        f"""
        WITH latest AS (
            SELECT country_code, MAX(snapshot_date) AS snapshot_date
            FROM `{dataset_id()}.country_top_artists`
            GROUP BY country_code
        )
        SELECT a.country_code, a.artist_name, a.rank
        FROM `{dataset_id()}.country_top_artists` a
        JOIN latest l
          ON l.country_code = a.country_code
         AND l.snapshot_date = a.snapshot_date
        WHERE a.rank <= {ARTIST_LIMIT}
        ORDER BY a.country_code, a.rank
        """
    )

    shares = {r["country_code"]: r for r in query("domestic_share_all")}

    top: dict[str, list[str]] = {}
    distinctive: dict[str, list[str]] = {}
    for r in genres:
        if r["popularity_rank"] <= 3:
            top.setdefault(r["country_code"], []).append(r["genre"])
        # Re-checked in Python, not left to the SQL: a row qualifying via
        # popularity_rank still carries a distinctiveness_rank, so filtering
        # on rank alone let universal genres (pop, rock) leak into the
        # distinctive list for countries with few genres.
        if r["distinctiveness_rank"] <= 3 and (r["distinctiveness"] or 0) > 0:
            distinctive.setdefault(r["country_code"], []).append(r["genre"])

    by_country: dict[str, list[str]] = {}
    for r in artists:
        by_country.setdefault(r["country_code"], []).append(r["artist_name"])

    return [
        {
            "code": c["code"],
            "name": c["name"],
            "artist_count": c["artist_count"],
            "top_genres": top.get(c["code"], []),
            "distinctive_genres": distinctive.get(c["code"], []),
            "top_artists": by_country.get(c["code"], []),
            "domestic_share": _domestic_share(shares.get(c["code"])),
            "cover_image": cover_image(by_country.get(c["code"], [])),
        }
        for c in countries
    ]


def build_detail(
    code: str,
    name: str,
    artist_count: int,
    artists: list[str],
    descriptions: dict,
) -> dict:
    """One country's full payload, including every genre panel."""
    popularity = _genre_breakdown(code, "popularity")
    distinctiveness = _genre_breakdown(code, "distinctiveness")

    tracks = run_query(
        f"""
        SELECT position, track_name, artist_name, daily_streams, days_on_chart
        FROM `{dataset_id()}.chart_entries`
        WHERE country_code = @code
          AND snapshot_date = (
              SELECT MAX(snapshot_date) FROM `{dataset_id()}.chart_entries`
              WHERE country_code = @code
          )
        ORDER BY position
        LIMIT {TRACK_LIMIT}
        """,
        {"code": code},
    )

    gems = query("hidden_gems", {"code": code, "limit": GEM_LIMIT})
    popularity_rows = query(
        "artist_popularity", {"code": code, "limit": POPULARITY_LIMIT}
    )
    share_rows = query("domestic_share", {"code": code})

    # Genre panels for every genre either donut can show, so opening one is a
    # local lookup rather than another request.
    #
    # One query for all of them, not one per genre. Called per genre this was
    # ~16 jobs per country and ~1,200 for a publish run, each paying the same
    # fixed BigQuery job overhead and rescanning the same two tables.
    wanted = {g["genre"] for g in popularity["genres"]}
    wanted |= {g["genre"] for g in distinctiveness["genres"]}

    rows = (
        query(
            "genre_artists",
            {
                "code": code,
                "genres": sorted(wanted),
                "limit": GENRE_ARTIST_LIMIT,
            },
        )
        if wanted
        else []
    )
    by_genre: dict[str, list[dict]] = {}
    for r in rows:
        by_genre.setdefault(r["genre"], []).append(
            {
                "artist": r["artist_name"],
                "streams": int(r["streams"]) if r.get("streams") else None,
                "best_position": r.get("best_position"),
            }
        )

    genre_details = {
        genre: {
            "genre": genre,
            "country_code": code,
            "country_name": name,
            "summary": descriptions.get(genre, {}).get("summary"),
            "url": descriptions.get(genre, {}).get("url"),
            "artists": by_genre.get(genre, []),
        }
        for genre in sorted(wanted)
    }

    return {
        "code": code,
        "name": name,
        "artist_count": artist_count,
        "artists": artists,
        "cover_image": cover_image(artists),
        "popularity": popularity,
        "distinctiveness": distinctiveness,
        "domestic_share": _domestic_share(share_rows[0] if share_rows else None),
        "top_tracks": [
            {
                "position": t["position"],
                "track": t["track_name"],
                "artist": t["artist_name"],
                "daily_streams": (
                    int(t["daily_streams"]) if t["daily_streams"] is not None else None
                ),
                "days_on_chart": t["days_on_chart"],
            }
            for t in tracks
        ],
        "hidden_gems": [
            {
                "artist": g["artist_name"],
                "streams": int(g["streams"] or 0),
                "best_position": g.get("best_position"),
                "country_count": g["country_count"],
                "total_countries": g["total_countries"],
                "gem_score": float(g["gem_score"] or 0),
            }
            for g in gems
        ],
        "artist_popularity": [
            {
                "artist": r["artist_name"],
                "streams": int(r["streams"]) if r.get("streams") else None,
                "lastfm_listeners": (
                    int(r["listeners"]) if r.get("listeners") else None
                ),
                "deezer_fans": (
                    int(r["deezer_fans"]) if r.get("deezer_fans") else None
                ),
            }
            for r in popularity_rows
        ],
        "genre_details": genre_details,
    }


def _write(root, relative: str, payload) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, default=str), encoding="utf-8")


def main() -> None:
    root = publish_dir()
    logger.info("Publishing to %s", root)

    summaries = build_summaries()
    _write(root, "countries.json", {"countries": summaries})
    logger.info("countries.json: %d countries", len(summaries))

    # Genre descriptions are reference data - identical for every country - so
    # they're fetched once here rather than inside each build_detail.
    descriptions = {
        r["genre"]: r
        for r in run_query(
            f"SELECT genre, summary, url FROM `{dataset_id()}.genres`"
        )
    }

    # Countries in parallel. Each one is ~7 sequential BigQuery jobs that
    # spend nearly all their time waiting on the API rather than computing, so
    # this is latency-bound and threads help. MAX_WORKERS is well under
    # BigQuery's concurrent-query limit; raising it further mostly queues.
    def publish_country(summary: dict) -> str:
        detail = build_detail(
            summary["code"],
            summary["name"],
            summary["artist_count"],
            summary["top_artists"],
            descriptions,
        )
        _write(root, f"country/{summary['code']}.json", detail)
        return summary["code"]

    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(publish_country, s) for s in summaries]
        for future in as_completed(futures):
            future.result()
            done += 1
            if done % 10 == 0:
                logger.info("  %d/%d countries", done, len(summaries))
    logger.info("country/*.json: %d files", len(summaries))

    globals_ = query("global_artists", {"limit": GLOBAL_ARTIST_LIMIT})
    _write(
        root,
        "artists-global.json",
        {
            "artists": [
                {
                    "artist": r["artist_name"],
                    "streams": int(r["streams"] or 0),
                    "previous_streams": (
                        int(r["previous_streams"])
                        if r.get("previous_streams") is not None
                        else None
                    ),
                    # NULL, not 0, until a second snapshot exists - "no
                    # comparison yet" is a different fact from "unchanged".
                    "delta": (
                        int(r["delta"]) if r.get("delta") is not None else None
                    ),
                    "country_count": r["country_count"],
                    "origin_country": r.get("origin_country"),
                }
                for r in globals_
            ]
        },
    )

    trending = query("trending_genres")
    _write(
        root,
        "genres-trending.json",
        {
            "genres": [
                {
                    "country_code": r["country_code"],
                    "genre": r["genre"],
                    "score": r["score"],
                    "previous_score": r["previous_score"],
                    "delta": r["delta"],
                }
                for r in trending
            ]
        },
    )
    logger.info("Done.")


if __name__ == "__main__":
    main()
