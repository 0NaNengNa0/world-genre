"""Serve per-country summaries for the API.

This module does NOT clean or reconcile anything itself - it just reads the
already-cleansed output of scripts/run_cleanse.py (data/processed/{code}.json)
and adds cover images. All genre normalization and Last.fm/MusicBrainz
reconciliation happens in app/services/cleansing.py; see that module and
scripts/run_cleanse.py for where the real work is.
"""
import json

from app.core.config import COUNTRIES, DATA_DIR

PROCESSED_DIR = DATA_DIR / "processed"
DEEZER_ARTISTS_PATH = DATA_DIR / "raw" / "deezer" / "artists.json"


def _load_deezer_images() -> dict[str, str]:
    if not DEEZER_ARTISTS_PATH.exists():
        return {}
    payload = json.loads(DEEZER_ARTISTS_PATH.read_text())
    return {
        name: data.get("picture_medium") or data.get("picture_big") or ""
        for name, data in payload.items()
        if data.get("picture_medium") or data.get("picture_big")
    }


_DEEZER_IMAGES = _load_deezer_images()


def _load_processed(code: str) -> dict:
    path = PROCESSED_DIR / f"{code}.json"
    if not path.exists():
        return {"artist_count": 0, "top_genres": [], "top_artists": []}
    payload = json.loads(path.read_text())
    return {
        "artist_count": payload.get("artist_count", 0),
        # cleansing.merge_genre_signals returns rich dicts (genre/score/sources)
        # so the /processed file keeps that lineage; the API contract only
        # promises plain genre names (see schemas/countries.py), so flatten here.
        "top_genres": [g["genre"] for g in payload.get("top_genres", [])],
        "top_artists": payload.get("top_artists", []),
    }


def _summarize(code: str, name: str) -> dict:
    data = _load_processed(code)
    top_artists = data.get("top_artists", [])
    cover_image = next(
        (_DEEZER_IMAGES[a] for a in top_artists if a in _DEEZER_IMAGES),
        None,
    )
    return {"code": code, "name": name, "cover_image": cover_image, **data}


def get_country_summaries() -> list[dict]:
    return [_summarize(c["kworb_code"], c["country_name"]) for c in COUNTRIES]
