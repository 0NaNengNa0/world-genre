"""Serve per-country summaries for the API.

Reads from Postgres - loaded by scripts/run_load.py from the cleansed
output of scripts/run_cleanse.py - rather than data/processed/*.json
directly. The API's read path shouldn't need to know those files exist at
all; that split is also why this module has no genre-normalization or
reconciliation logic in it (see app/services/cleansing.py for that).

Cover images are the one thing still read from flat files - they're a simple
name->URL lookup with no history/versioning need, so warehousing them would
be overhead with no payoff.
"""
import json

from app.core.config import DATA_DIR
from app.core.db import get_connection
from app.services.extractors import deezer

DEEZER_ARTISTS_PATH = DATA_DIR / "raw" / "deezer" / "artists.json"
WIKIDATA_ARTISTS_PATH = DATA_DIR / "raw" / "wikidata" / "artists.json"

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


def _load_wikidata_images() -> dict[str, str]:
    """Artist name -> Wikimedia Commons thumbnail, for artists Deezer lacks.

    See app/services/extractors/wikidata.py for why this exists and why it's
    a fallback rather than the primary source.
    """
    if not WIKIDATA_ARTISTS_PATH.exists():
        return {}
    payload = json.loads(WIKIDATA_ARTISTS_PATH.read_text())
    return {
        name: data["image"]
        for name, data in payload.items()
        if isinstance(data, dict) and data.get("image")
    }


_image_cache: tuple[tuple[float, float], dict[str, str]] | None = None


def _mtime(path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _artist_images() -> dict[str, str]:
    """Merged artist->image lookup, Deezer first then Wikidata.

    Deezer wins where it has a real photo: it covers current chart artists far
    better, whereas Commons requires a free licence and skews toward older and
    more Western acts. Wikidata only fills gaps.

    Cached against both files' mtimes. Loading once at import was wrong in a
    way that only shows up in a long-running process: the pipeline rewrites
    these files on every run, so the API kept serving whatever mapping existed
    when it started - indefinitely in production, since nothing reloads it.
    Two stat() calls per request keeps the file reads rare without going
    stale.
    """
    global _image_cache
    stamps = (_mtime(DEEZER_ARTISTS_PATH), _mtime(WIKIDATA_ARTISTS_PATH))
    if _image_cache is None or _image_cache[0] != stamps:
        merged = _load_wikidata_images()
        merged.update(_load_deezer_images())  # Deezer takes precedence
        _image_cache = (stamps, merged)
    return _image_cache[1]


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

_DOMESTIC_SHARE_SQL = (
    DATA_DIR.parent / "sql" / "queries" / "domestic_share.sql"
).read_text()

_DOMESTIC_SHARE_ALL_SQL = (
    DATA_DIR.parent / "sql" / "queries" / "domestic_share_all.sql"
).read_text()

_HIDDEN_GEMS_SQL = (DATA_DIR.parent / "sql" / "queries" / "hidden_gems.sql").read_text()

_GLOBAL_ARTISTS_SQL = (
    DATA_DIR.parent / "sql" / "queries" / "global_artists.sql"
).read_text()

DETAIL_GEM_LIMIT = 10
GLOBAL_ARTIST_LIMIT = 40


def get_global_artists(limit: int = GLOBAL_ARTIST_LIMIT) -> list[dict]:
    """Biggest artists worldwide by charted streams.

    Summed across every country's chart, so an artist charting modestly in
    forty countries can outrank one dominating a single large market.
    `delta` is None until a second snapshot exists - see the query.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_GLOBAL_ARTISTS_SQL, {"limit": limit})
            rows = cur.fetchall()

    return [
        {
            "artist": r[0],
            "streams": int(r[1]),
            "previous_streams": int(r[2]) if r[2] is not None else None,
            "delta": int(r[3]) if r[3] is not None else None,
            "country_count": r[4],
            "origin_country": r[5],
        }
        for r in rows
    ]


def _summarize_share(total, classified, domestic, entries, classified_entries) -> dict | None:
    """Shared shaping for the one-country and all-countries share queries, so
    the two can't drift apart in how they round or when they give up."""
    if not classified:
        return None
    return {
        "domestic_percentage": round(100.0 * float(domestic) / float(classified), 2),
        "coverage_percentage": round(100.0 * float(classified) / float(total), 2)
        if total
        else 0.0,
        "classified_entries": classified_entries,
        "total_entries": entries,
    }


def _fetch_domestic_share(cur, code: str) -> dict | None:
    """Share of a country's chart streams going to its own artists.

    Returns None when nothing can be said yet - no chart data, or no artist
    origins resolved. That's a real state early on: the artists dimension
    fills in over successive pipeline runs (MusicBrainz is rate-limited), so
    the honest answer at first is "not enough data", not "0 percent
    domestic".
    """
    cur.execute(_DOMESTIC_SHARE_SQL, {"code": code})
    row = cur.fetchone()
    if row is None:
        return None
    return _summarize_share(*row)

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
DETAIL_TRACK_LIMIT = 20


def get_country_detail(
    code: str,
    artist_limit: int = DETAIL_ARTIST_LIMIT,
    genre_limit: int = DETAIL_GENRE_LIMIT,
    track_limit: int = DETAIL_TRACK_LIMIT,
    gem_limit: int = DETAIL_GEM_LIMIT,
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
            domestic_share = _fetch_domestic_share(cur, country_code)

            # Artists this country streams heavily that barely chart
            # elsewhere - the artist-level counterpart to genre
            # distinctiveness, and the answer to "what would I only find
            # here".
            cur.execute(_HIDDEN_GEMS_SQL, {"code": country_code, "limit": gem_limit})
            hidden_gems = [
                {
                    "artist": r[0],
                    "streams": int(r[1]),
                    "best_position": r[2],
                    "country_count": r[3],
                    "total_countries": r[4],
                    "gem_score": float(r[5]),
                }
                for r in cur.fetchall()
            ]

            # Straight from the fact table - actual charting tracks with
            # measured streams, as opposed to the artist rankings elsewhere
            # which are derived from Last.fm listener counts.
            cur.execute(
                """
                SELECT position, track_name, artist_name, daily_streams, days_on_chart
                FROM chart_entries
                WHERE country_code = %s
                  AND snapshot_date = (
                      SELECT MAX(snapshot_date) FROM chart_entries
                      WHERE country_code = %s
                  )
                ORDER BY position
                LIMIT %s
                """,
                (country_code, country_code, track_limit),
            )
            top_tracks = [
                {
                    "position": r[0],
                    "track": r[1],
                    "artist": r[2],
                    "daily_streams": r[3],
                    "days_on_chart": r[4],
                }
                for r in cur.fetchall()
            ]

    images = _artist_images()
    cover_image = next((images[a] for a in artists if a in images), None)

    return {
        "code": country_code,
        "name": name,
        "artist_count": artist_count,
        "artists": artists,
        "popularity": popularity,
        "distinctiveness": distinctiveness,
        "domestic_share": domestic_share,
        "top_tracks": top_tracks,
        "hidden_gems": hidden_gems,
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

            # One grouped query for all countries, not one per country - the
            # map colours 76 shapes by this, and per-country would rebuild
            # the N+1 pattern this endpoint was rewritten to remove.
            cur.execute(_DOMESTIC_SHARE_ALL_SQL)
            share_rows = cur.fetchall()

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

    shares: dict[str, dict] = {}
    for code, total, classified, domestic, entries, classified_entries in share_rows:
        share = _summarize_share(total, classified, domestic, entries, classified_entries)
        if share:
            shares[code] = share

    images = _artist_images()
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
                # Present so the map can colour by it; None until enough
                # artist origins are resolved to say anything.
                "domestic_share": shares.get(code),
                # Only what the card renders. Returning all 100 meant ~7,600
                # artist strings in a payload that displays three per country.
                "top_artists": scanned[:SUMMARY_ARTISTS_RETURNED],
            }
        )
    return summaries
