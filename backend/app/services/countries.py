"""Serve per-country summaries for the API.

Reads from Postgres - loaded by scripts/run_load.py from the cleansed
output of scripts/run_cleanse.py - rather than data/processed/*.json
directly. The API's read path shouldn't need to know those files exist at
all; that split is also why this module has no genre-normalization or
reconciliation logic in it (see app/services/cleansing.py for that).

Deezer cover images are the one thing still read from a flat file - they're
a simple name->URL lookup with no history/versioning need, so warehousing
them would be overhead with no payoff.
"""
import json

from app.core.config import DATA_DIR
from app.core.db import get_connection
from app.services.extractors import deezer

DEEZER_ARTISTS_PATH = DATA_DIR / "raw" / "deezer" / "artists.json"

# country_genre_scores holds the union of both rankings (a genre can be in the
# popularity list, the distinctiveness list, or both), so each list is longer
# than what either view should show. Truncate per-view rather than assuming
# the table's row count is the display count.
TOP_N_GENRES = 5


def _load_deezer_images() -> dict[str, str]:
    """Artist name -> real cover image URL.

    Filters Deezer's empty-hash placeholder (see deezer.has_real_picture) -
    a plain truthiness check keeps it, because Deezer returns a well-formed
    URL for artists it has no photo of, which then renders as a blank square.
    """
    if not DEEZER_ARTISTS_PATH.exists():
        return {}
    payload = json.loads(DEEZER_ARTISTS_PATH.read_text())
    images = {}
    for name, data in payload.items():
        url = deezer.pick_picture(data)
        if url:
            images[name] = url
    return images


_deezer_cache: tuple[float, dict[str, str]] | None = None


def _deezer_images() -> dict[str, str]:
    """Cached artist-image lookup, invalidated when the file changes.

    Loading this once at import was wrong in a way that only shows up in a
    long-running process: the pipeline rewrites artists.json on every run, so
    the API kept serving the image mapping from whenever it happened to start
    - indefinitely in production, since nothing reloads it. Keying the cache
    on mtime costs one stat() per request and keeps the file read rare.
    """
    global _deezer_cache
    try:
        mtime = DEEZER_ARTISTS_PATH.stat().st_mtime
    except OSError:
        return {}
    if _deezer_cache is None or _deezer_cache[0] != mtime:
        _deezer_cache = (mtime, _load_deezer_images())
    return _deezer_cache[1]


def _fetch_latest_artist_count(cur, code: str) -> int:
    cur.execute(
        """
        SELECT artist_count FROM country_snapshots
        WHERE country_code = %s
        ORDER BY snapshot_date DESC
        LIMIT 1
        """,
        (code,),
    )
    row = cur.fetchone()
    return row[0] if row else 0


_GENRE_SHARES_SQL = (
    DATA_DIR.parent / "sql" / "queries" / "country_genre_shares.sql"
).read_text()

# The only values ever substituted into {weight_column} in that query. A
# column name can't be a bind parameter, so it goes in by string formatting -
# which is only safe because it can never be caller-supplied text.
_WEIGHT_COLUMNS = {"score", "distinctiveness"}


def _fetch_genre_breakdown(cur, code: str, weight_column: str, limit: int) -> dict:
    """One country's genres ranked by `weight_column`, as shares of the total.

    Returns {"genres": [...], "other_percentage": float,
    "other_genre_count": int} - the leftover slice so a chart of the top N
    still totals 100 instead of silently dropping the tail.
    """
    if weight_column not in _WEIGHT_COLUMNS:
        raise ValueError(f"unsupported weight column: {weight_column}")

    # str.replace, not str.format: the query file documents the endpoint as
    # "/api/countries/{code}", and .format() would try to substitute that
    # comment as a field and raise KeyError. replace() only touches the exact
    # token, so prose in the file can't collide with it.
    cur.execute(
        _GENRE_SHARES_SQL.replace("{weight_column}", weight_column),
        {"code": code, "limit": limit},
    )
    # Mapped by column name rather than tuple position: the query lives in its
    # own .sql file, so adding a column there would silently shift every index.
    columns = [c.name for c in cur.description]
    rows = [dict(zip(columns, r)) for r in cur.fetchall()]

    genres = [
        {
            "genre": r["genre"],
            "score": r["score"],
            "distinctiveness": float(r["distinctiveness"]),
            "sources": r["sources"] or [],
            "percentage": float(r["percentage"] or 0.0),
        }
        for r in rows
    ]
    total_genre_count = rows[0]["genre_count"] if rows else 0
    listed = sum(g["percentage"] for g in genres)
    return {
        "genres": genres,
        # Clamp at 0: rounding each slice to 2dp can push the sum a hair over
        # 100. And with no genres at all this is 0, not 100 - otherwise the
        # chart draws a full ring labelled "other (0 genres)" instead of
        # showing its empty state.
        "other_percentage": round(max(100.0 - listed, 0.0), 2) if rows else 0.0,
        "other_genre_count": max(total_genre_count - len(genres), 0),
    }

# How many artists / genre slices the detail view gets. More genre slices than
# this and a pie chart stops being readable - the remainder is reported as a
# single "other" slice instead of being silently dropped.
DETAIL_ARTIST_LIMIT = 100
DETAIL_GENRE_LIMIT = 10


def get_country_detail(
    code: str,
    artist_limit: int = DETAIL_ARTIST_LIMIT,
    genre_limit: int = DETAIL_GENRE_LIMIT,
) -> dict | None:
    """Full detail for one country, or None if it isn't in the warehouse.

    Split from get_country_summaries rather than fattening it: the grid
    fetches all 76 countries at once, and returning 100 artists each there
    would be a large payload for data the grid never renders.

    Returns TWO genre breakdowns rather than one, because the chart can be
    read either way and they disagree almost completely:

      popularity      - share of what the country actually plays. Near
                        identical everywhere; pop and rock dominate.
      distinctiveness - share of what sets it apart from the other countries
                        (see cleansing.score_distinctiveness). Genres common
                        to everywhere score 0 and drop out entirely.

    Both come from the same SQL, which computes each share against the
    country's true total before limiting - so each breakdown's slices plus
    its `other_percentage` total 100, rather than being inflated by the
    truncation.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT code, name FROM countries WHERE code = %s", (code,))
            row = cur.fetchone()
            if row is None:
                return None
            country_code, name = row

            popularity = _fetch_genre_breakdown(cur, country_code, "score", genre_limit)
            distinctiveness = _fetch_genre_breakdown(
                cur, country_code, "distinctiveness", genre_limit
            )

            cur.execute(
                """
                SELECT artist_name FROM country_top_artists
                WHERE country_code = %s
                  AND snapshot_date = (
                      SELECT MAX(snapshot_date)
                      FROM country_top_artists WHERE country_code = %s
                  )
                ORDER BY rank
                LIMIT %s
                """,
                (country_code, country_code, artist_limit),
            )
            artists = [r[0] for r in cur.fetchall()]

            artist_count = _fetch_latest_artist_count(cur, country_code)

    images = _deezer_images()
    cover_image = next((images[a] for a in artists if a in images), None)

    return {
        "code": country_code,
        "name": name,
        "artist_count": artist_count,
        "artists": artists,
        "popularity": popularity,
        "distinctiveness": distinctiveness,
        "cover_image": cover_image,
    }


# How many artists the grid endpoint returns per country, and how many it
# looks at when picking a cover image. The cover is the first artist that has
# a Deezer photo, and roughly 7 percent of artists don't, so it scans deeper
# than it returns rather than showing a placeholder whenever artist #1 happens
# to be one of them.
SUMMARY_ARTISTS_RETURNED = 5
SUMMARY_ARTISTS_SCANNED = 20


def get_country_summaries() -> list[dict]:
    """One row per country loaded into the warehouse, using each country's
    most recent snapshot_date independently (countries aren't guaranteed to
    have been loaded on the exact same day if the pipeline was rerun
    selectively).

    Three queries total, not three per country. The straightforward version
    of this looped over countries issuing a few queries each - 305 round
    trips for 76 countries, every one of them re-deriving that country's
    latest snapshot_date with a correlated subquery. That barely shows up
    against a local socket and turns into hundreds of milliseconds against a
    networked database, where round-trip latency dominates. Ranking with
    window functions instead keeps the query count flat as countries are
    added.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.code, c.name, COALESCE(s.artist_count, 0)
                FROM countries c
                LEFT JOIN LATERAL (
                    SELECT artist_count
                    FROM country_snapshots
                    WHERE country_code = c.code
                    ORDER BY snapshot_date DESC
                    LIMIT 1
                ) s ON TRUE
                ORDER BY c.name
                """
            )
            countries = cur.fetchall()

            # Both genre rankings in one pass: two ROW_NUMBERs over the same
            # rows, filtered to whichever made either top list.
            cur.execute(
                """
                WITH latest AS (
                    SELECT country_code, MAX(snapshot_date) AS snapshot_date
                    FROM country_genre_scores
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
                    FROM country_genre_scores g
                    JOIN latest l
                      ON l.country_code = g.country_code
                     AND l.snapshot_date = g.snapshot_date
                )
                SELECT
                    country_code, genre, popularity_rank, distinctiveness_rank,
                    distinctiveness
                FROM ranked
                WHERE popularity_rank <= %s
                   OR (distinctiveness_rank <= %s AND distinctiveness > 0)
                ORDER BY country_code, popularity_rank
                """,
                (TOP_N_GENRES, TOP_N_GENRES),
            )
            genre_rows = cur.fetchall()

            cur.execute(
                """
                WITH latest AS (
                    SELECT country_code, MAX(snapshot_date) AS snapshot_date
                    FROM country_top_artists
                    GROUP BY country_code
                )
                SELECT a.country_code, a.artist_name
                FROM country_top_artists a
                JOIN latest l
                  ON l.country_code = a.country_code
                 AND l.snapshot_date = a.snapshot_date
                WHERE a.rank <= %s
                ORDER BY a.country_code, a.rank
                """,
                (SUMMARY_ARTISTS_SCANNED,),
            )
            artist_rows = cur.fetchall()

    top_genres: dict[str, list[tuple[int, str]]] = {}
    distinctive: dict[str, list[tuple[int, str]]] = {}
    for code, genre, pop_rank, dist_rank, distinctiveness in genre_rows:
        if pop_rank <= TOP_N_GENRES:
            top_genres.setdefault(code, []).append((pop_rank, genre))
        # The `distinctiveness > 0` guard has to be repeated here, not just in
        # the WHERE: a row selected for its popularity rank still carries a
        # distinctiveness rank, so universal genres like pop and rock would
        # otherwise slip into the list of what makes a country distinctive.
        if dist_rank <= TOP_N_GENRES and distinctiveness > 0:
            distinctive.setdefault(code, []).append((dist_rank, genre))

    artists: dict[str, list[str]] = {}
    for code, artist_name in artist_rows:
        artists.setdefault(code, []).append(artist_name)

    images = _deezer_images()
    summaries = []
    for code, name, artist_count in countries:
        scanned = artists.get(code, [])
        summaries.append(
            {
                "code": code,
                "name": name,
                "cover_image": next(
                    (images[a] for a in scanned if a in images), None
                ),
                "artist_count": artist_count,
                "top_genres": [g for _, g in sorted(top_genres.get(code, []))],
                "distinctive_genres": [g for _, g in sorted(distinctive.get(code, []))],
                # Only what the card renders. Returning all 100 meant ~7,600
                # artist strings in a payload that displays three per country.
                "top_artists": scanned[:SUMMARY_ARTISTS_RETURNED],
            }
        )
    return summaries
