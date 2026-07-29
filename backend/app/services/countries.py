"""Aggregate per-country summaries from raw extractor output.

Primary genre source: Last.fm crowd-sourced tags.
Secondary: Spotify artist genres (sparse post-2026, used as fallback only).
"""
import json
from collections import Counter

from app.core.config import COUNTRIES, DATA_DIR

LASTFM_DIR = DATA_DIR / "raw" / "lastfm"
SPOTIFY_DIR = DATA_DIR / "raw" / "spotify"
DEEZER_ARTISTS_PATH = DATA_DIR / "raw" / "deezer" / "artists.json"
TOP_N_GENRES = 5
TOP_N_ARTISTS = 5


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


def _from_lastfm(code: str) -> dict | None:
    path = LASTFM_DIR / f"{code}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    tags_by_artist: dict[str, list[dict]] = payload.get("tags_by_artist", {})

    genre_counts: Counter[str] = Counter()
    for tags in tags_by_artist.values():
        for tag in tags[:5]:
            name = tag.get("name") if isinstance(tag, dict) else None
            if name:
                genre_counts[name.lower()] += 1

    artist_names = [a["name"] for a in payload.get("artists", [])]
    return {
        "artist_count": len(artist_names),
        "top_genres": [g for g, _ in genre_counts.most_common(TOP_N_GENRES)],
        "top_artists": artist_names[:TOP_N_ARTISTS],
    }


def _from_spotify(code: str) -> dict | None:
    path = SPOTIFY_DIR / f"{code}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    genres_by_artist: dict[str, list[str]] = payload.get("genres_by_artist", {})

    genre_counts: Counter[str] = Counter()
    for genres in genres_by_artist.values():
        genre_counts.update(genres)

    return {
        "artist_count": len(genres_by_artist),
        "top_genres": [g for g, _ in genre_counts.most_common(TOP_N_GENRES)],
        "top_artists": list(genres_by_artist.keys())[:TOP_N_ARTISTS],
    }


def _summarize(code: str, name: str) -> dict:
    data = _from_lastfm(code) or _from_spotify(code) or {
        "artist_count": 0,
        "top_genres": [],
        "top_artists": [],
    }
    top_artists = data.get("top_artists", [])
    cover_image = next(
        (_DEEZER_IMAGES[a] for a in top_artists if a in _DEEZER_IMAGES),
        None,
    )
    return {"code": code, "name": name, "cover_image": cover_image, **data}


def get_country_summaries() -> list[dict]:
    return [_summarize(c["kworb_code"], c["lastfm_name"]) for c in COUNTRIES]
