"""Cleanse + reconcile raw extractor output into one processed record per
country.

    data/raw/{lastfm,musicbrainz,kworb}/*.json  ->  data/processed/{code}.json

All the actual normalization/reconciliation logic lives in
app/services/cleansing.py - this script just loads the raw files, calls it,
and writes the result. Genre source priority: both Last.fm and MusicBrainz
are merged (see cleansing.merge_genre_signals), not one falling back to the
other - that's the point of having two independent genre signals at all.

Also writes two things a plain "loads, transforms, writes" script usually
skips, both deliberately:

  1. A per-run data-quality report (data/processed/_quality_report.json) -
     per-country artist counts and genre-tag unclassified rates, plus a
     summary flagging zero-data countries and the worst unclassified rate.
     Without this, a source going quiet or a taxonomy gap (both of which
     have actually happened while building this pipeline - see the South
     Korea country-name bug) is invisible until someone eyeballs the JSON
     output by hand.
  2. A dated history snapshot (data/processed/history/{code}/{date}.json)
     next to the "latest" file the API reads. The API only ever needs
     "latest", but a project about *genre interest* is much more
     interesting with a trend to show ("has hip-hop grown in country X
     since launch?") than a single point-in-time snapshot - and capturing
     that history costs nothing extra at cleanse time, while trying to
     reconstruct it later from nothing would be impossible.

Run from the backend/ directory, after the extractors:
    python -m scripts.run_extract_kworb
    python -m scripts.run_extract_lastfm
    python -m scripts.run_extract_musicbrainz
    python -m scripts.run_cleanse
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import COUNTRIES, DATA_DIR
from app.services import cleansing

LASTFM_DIR = DATA_DIR / "raw" / "lastfm"
MUSICBRAINZ_DIR = DATA_DIR / "raw" / "musicbrainz"
KWORB_DIR = DATA_DIR / "raw" / "kworb"
OUTPUT_DIR = DATA_DIR / "processed"
HISTORY_DIR = OUTPUT_DIR / "history"
QUALITY_REPORT_PATH = OUTPUT_DIR / "_quality_report.json"

# How much to PERSIST per country. Deliberately deeper than any single view
# shows: the grid renders 5 and the country detail view 100 artists / 10
# genres, and re-running the pipeline to recover a number that was truncated
# at write time is expensive (MusicBrainz is ~1 req/sec). Storing the full
# depth once and limiting per-query in SQL is much cheaper than the reverse.
TOP_N_ARTISTS = 100  # matches run_extract_lastfm's per-country sample depth
# Genres are stored in full (no cap): a country only has ~30-40 after
# bucketing, so the whole distribution is a few thousand rows across all 76
# countries - and percentage share is only correct when divided by the real
# total rather than a truncated one.

# Above this unclassified-tag rate, a country's run is flagged in the
# quality report summary - not a hard failure, just a "look at this" signal.
UNCLASSIFIED_RATE_WARN_THRESHOLD = 0.5

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("run_cleanse")


def _load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def _artists_from_lastfm(payload: dict) -> list[str]:
    return [a["name"] for a in payload.get("artists", []) if a.get("name")]


def _artists_from_kworb(payload: dict) -> list[str]:
    """Fallback path for countries with no Last.fm data - parses + cleanses
    artist names straight out of the raw chart rows.

    Row parsing is shared with run_extract_musicbrainz.py via
    cleansing.parse_artist_from_chart_row; this used to have its own copy
    that only split on " - ", so rows written without spaces around the dash
    ("BTS-NORMAL") survived intact here while the MusicBrainz extractor
    split them correctly - the two paths disagreed about the same row.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for row in payload.get("rows", []):
        if len(row) < 3:
            continue
        name = cleansing.normalize_artist_name(
            cleansing.parse_artist_from_chart_row(row[2])
        )
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _process_country(country: dict) -> tuple[dict, dict, list[dict]]:
    """Pass 1 for a single country.

    Returns (partial_record, quality_stats, full_genre_distribution). The
    record is deliberately incomplete - genre ranking can't be finished
    until every country has been read, because distinctiveness is defined
    relative to the others (see main()).
    """
    code, name = country["kworb_code"], country["country_name"]

    lastfm = _load(LASTFM_DIR / f"{code}.json")
    musicbrainz = _load(MUSICBRAINZ_DIR / f"{code}.json")
    kworb = _load(KWORB_DIR / f"{code}.json")

    genre_stats: dict = {}
    artists_by_genre: dict[str, set[str]] = {}
    # No top_n - the full distribution is needed so pass 2 can rank the long
    # tail, where the genres that actually differentiate countries live.
    all_genres = cleansing.merge_genre_signals(
        lastfm.get("tags_by_artist", {}),
        musicbrainz.get("genres_by_artist", {}),
        stats=genre_stats,
        artists_by_genre=artists_by_genre,
    )

    artist_names = _artists_from_lastfm(lastfm) or _artists_from_kworb(kworb)

    record = {
        "country_code": code,
        "country_name": name,
        "artist_count": len(artist_names),
        "top_artists": artist_names[:TOP_N_ARTISTS],
        # Which artists caused each genre to score here. Sorted rather than
        # left as a set so the JSON is stable between runs and diffable.
        "artists_by_genre": {
            genre: sorted(artists) for genre, artists in sorted(artists_by_genre.items())
        },
    }
    stats = {
        "artist_count": len(artist_names),
        "total_genre_tags": genre_stats.get("total_tags", 0),
        "unclassified_genre_tags": genre_stats.get("unclassified_tags", 0),
        "unclassified_rate": genre_stats.get("unclassified_rate", 0.0),
        "distinct_genres": len(all_genres),
    }
    return record, stats, all_genres


def _write_quality_report(per_country_stats: dict[str, dict]) -> None:
    rated = {
        code: stats
        for code, stats in per_country_stats.items()
        if stats["total_genre_tags"] > 0
    }
    zero_artist_countries = sorted(
        code for code, stats in per_country_stats.items() if stats["artist_count"] == 0
    )
    high_unclassified = sorted(
        (
            {"country": code, "unclassified_rate": stats["unclassified_rate"]}
            for code, stats in rated.items()
            if stats["unclassified_rate"] > UNCLASSIFIED_RATE_WARN_THRESHOLD
        ),
        key=lambda entry: entry["unclassified_rate"],
        reverse=True,
    )
    average_unclassified_rate = (
        round(sum(s["unclassified_rate"] for s in rated.values()) / len(rated), 4)
        if rated
        else 0.0
    )

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "countries_processed": len(per_country_stats),
        "countries": per_country_stats,
        "summary": {
            "zero_artist_countries": zero_artist_countries,
            "average_unclassified_rate": average_unclassified_rate,
            "countries_above_unclassified_threshold": high_unclassified,
        },
    }
    QUALITY_REPORT_PATH.write_text(json.dumps(report, indent=2))

    if zero_artist_countries:
        logger.warning("Zero artists resolved for: %s", ", ".join(zero_artist_countries))
    if high_unclassified:
        worst = high_unclassified[0]
        logger.warning(
            "%d countr%s above %.0f%% unclassified genre-tag rate (worst: %s at %.0f%%)",
            len(high_unclassified),
            "y" if len(high_unclassified) == 1 else "ies",
            UNCLASSIFIED_RATE_WARN_THRESHOLD * 100,
            worst["country"],
            worst["unclassified_rate"] * 100,
        )
    logger.info(
        "Quality report written to %s (avg unclassified rate: %.1f%%)",
        QUALITY_REPORT_PATH.resolve(),
        average_unclassified_rate * 100,
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()

    per_country_stats: dict[str, dict] = {}
    records: dict[str, dict] = {}
    genres_by_country: dict[str, list[dict]] = {}

    # --- Pass 1: read every country, build its full genre distribution ---
    for country in COUNTRIES:
        code = country["kworb_code"]
        record, stats, all_genres = _process_country(country)
        per_country_stats[code] = stats
        records[code] = record
        genres_by_country[code] = all_genres

        if stats["artist_count"] == 0:
            logger.warning("%s: no artists resolved", code)
        elif stats["unclassified_rate"] > UNCLASSIFIED_RATE_WARN_THRESHOLD:
            logger.warning(
                "%s: %.0f%% of genre tags unclassified", code, stats["unclassified_rate"] * 100
            )
        else:
            logger.info(
                "%s: %d artists, %d genres, %.0f%% tags unclassified",
                code,
                stats["artist_count"],
                stats["distinct_genres"],
                stats["unclassified_rate"] * 100,
            )

    # --- Pass 2: score each country against the others, then write ---
    # Only countries that produced genres count toward the denominator -
    # including empty ones would inflate total_countries and understate every
    # genre's document frequency, making everything look more distinctive
    # than it is.
    contributing = {c: rows for c, rows in genres_by_country.items() if rows}
    document_frequency = cleansing.genre_document_frequency(contributing)
    total_countries = len(contributing)
    logger.info(
        "Scoring distinctiveness across %d countries with genre data", total_countries
    )

    for code, record in records.items():
        ranked = cleansing.score_distinctiveness(
            genres_by_country[code], document_frequency, total_countries
        )
        # Two views of the same data: what they listen to, and what sets them
        # apart. Popularity alone is near-identical everywhere (pop/rock top
        # almost every country), so keeping only it would hide the whole point
        # - and keeping only distinctiveness would misrepresent what's
        # actually played.
        # One list holding every genre with BOTH scores, rather than two
        # pre-truncated top-N lists. Two reasons:
        #  - percentage share (the detail view's pie chart) has to divide by
        #    the true total across all genres; dividing by a truncated total
        #    silently inflates every slice.
        #  - "top 5 by popularity" and "top 10 by distinctiveness" then differ
        #    only by an ORDER BY / LIMIT in SQL, instead of being baked in
        #    here where changing a view means re-running the whole pipeline.
        record["genres"] = sorted(ranked, key=lambda r: r["score"], reverse=True)

        (OUTPUT_DIR / f"{code}.json").write_text(json.dumps(record, indent=2))

        history_dir = HISTORY_DIR / code
        history_dir.mkdir(parents=True, exist_ok=True)
        (history_dir / f"{today}.json").write_text(json.dumps(record, indent=2))

    _write_quality_report(per_country_stats)
    logger.info("Done. Cleansed data in %s", OUTPUT_DIR.resolve())


if __name__ == "__main__":
    main()
