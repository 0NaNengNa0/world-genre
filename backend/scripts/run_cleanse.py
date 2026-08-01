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

TOP_N_ARTISTS = 5

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
    artist names straight out of the raw chart rows."""
    seen: set[str] = set()
    ordered: list[str] = []
    for row in payload.get("rows", []):
        if len(row) < 3:
            continue
        raw = row[2].split(" - ", 1)[0] if " - " in row[2] else row[2]
        name = cleansing.normalize_artist_name(raw)
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _process_country(country: dict) -> tuple[dict, dict]:
    """Returns (processed_record, quality_stats) for one country."""
    code, name = country["kworb_code"], country["country_name"]

    lastfm = _load(LASTFM_DIR / f"{code}.json")
    musicbrainz = _load(MUSICBRAINZ_DIR / f"{code}.json")
    kworb = _load(KWORB_DIR / f"{code}.json")

    genre_stats: dict = {}
    top_genres = cleansing.merge_genre_signals(
        lastfm.get("tags_by_artist", {}),
        musicbrainz.get("genres_by_artist", {}),
        top_n=TOP_N_ARTISTS,
        stats=genre_stats,
    )

    artist_names = _artists_from_lastfm(lastfm) or _artists_from_kworb(kworb)

    record = {
        "country_code": code,
        "country_name": name,
        "artist_count": len(artist_names),
        "top_artists": artist_names[:TOP_N_ARTISTS],
        "top_genres": top_genres,
    }
    stats = {
        "artist_count": len(artist_names),
        "total_genre_tags": genre_stats.get("total_tags", 0),
        "unclassified_genre_tags": genre_stats.get("unclassified_tags", 0),
        "unclassified_rate": genre_stats.get("unclassified_rate", 0.0),
    }
    return record, stats


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

    for country in COUNTRIES:
        code = country["kworb_code"]
        record, stats = _process_country(country)
        per_country_stats[code] = stats

        (OUTPUT_DIR / f"{code}.json").write_text(json.dumps(record, indent=2))

        history_dir = HISTORY_DIR / code
        history_dir.mkdir(parents=True, exist_ok=True)
        (history_dir / f"{today}.json").write_text(json.dumps(record, indent=2))

        if stats["artist_count"] == 0:
            logger.warning("%s: no artists resolved", code)
        elif stats["unclassified_rate"] > UNCLASSIFIED_RATE_WARN_THRESHOLD:
            logger.warning(
                "%s: %.0f%% of genre tags unclassified", code, stats["unclassified_rate"] * 100
            )
        else:
            logger.info(
                "%s: %d artists, %.0f%% genre tags unclassified",
                code,
                stats["artist_count"],
                stats["unclassified_rate"] * 100,
            )

    _write_quality_report(per_country_stats)
    logger.info("Done. Cleansed data in %s", OUTPUT_DIR.resolve())


if __name__ == "__main__":
    main()
