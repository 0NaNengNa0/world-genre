"""Cleanse + reconcile raw extractor output into one processed record per
country.

    data/raw/{lastfm,musicbrainz,kworb}/*.json  ->  data/processed/{code}.json

All the actual normalization/reconciliation logic lives in
app/services/cleansing.py - this script just loads the raw files, calls it,
and writes the result. Genre source priority: both Last.fm and MusicBrainz
are merged (see cleansing.merge_genre_signals), not one falling back to the
other - that's the point of having two independent genre signals at all.

Run from the backend/ directory, after the extractors:
    python -m scripts.run_extract_kworb
    python -m scripts.run_extract_lastfm
    python -m scripts.run_extract_musicbrainz
    python -m scripts.run_cleanse
"""
import json
from pathlib import Path

from app.core.config import COUNTRIES, DATA_DIR
from app.services import cleansing

LASTFM_DIR = DATA_DIR / "raw" / "lastfm"
MUSICBRAINZ_DIR = DATA_DIR / "raw" / "musicbrainz"
KWORB_DIR = DATA_DIR / "raw" / "kworb"
OUTPUT_DIR = DATA_DIR / "processed"

TOP_N_ARTISTS = 5


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


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for country in COUNTRIES:
        code, name = country["kworb_code"], country["country_name"]

        lastfm = _load(LASTFM_DIR / f"{code}.json")
        musicbrainz = _load(MUSICBRAINZ_DIR / f"{code}.json")
        kworb = _load(KWORB_DIR / f"{code}.json")

        top_genres = cleansing.merge_genre_signals(
            lastfm.get("tags_by_artist", {}),
            musicbrainz.get("genres_by_artist", {}),
        )

        artist_names = _artists_from_lastfm(lastfm) or _artists_from_kworb(kworb)

        (OUTPUT_DIR / f"{code}.json").write_text(json.dumps({
            "country_code": code,
            "country_name": name,
            "artist_count": len(artist_names),
            "top_artists": artist_names[:TOP_N_ARTISTS],
            "top_genres": top_genres,
        }, indent=2))

    print(f"Done. Cleansed data in {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
