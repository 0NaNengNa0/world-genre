"""Enrich chart artists with genre tags from MusicBrainz.

Pipeline:
1. Prefer data/raw/lastfm/{code}.json - Last.fm already returns an `mbid`
   per artist, so we can skip straight to the genre lookup (1 API call).
2. Fall back to data/raw/kworb/{code}.json for countries with no Last.fm
   data - parse the artist name from the chart row and search MusicBrainz
   by name first to find an MBID (2 API calls).
3. Cache every resolved artist to disk so reruns skip completed work.
4. Assemble per-country output shaped like the old Spotify output
   (genres_by_artist: name -> list[str]) so app/services/countries.py's
   fallback logic barely had to change.

Rate-limit strategy: MusicBrainz allows ~1 req/sec, strictly enforced.
Serial, single-threaded, with a pacing sleep after every HTTP call (not
just per artist - some artists need 2 calls).

Run from the backend/ directory:
    python -m scripts.run_extract_lastfm       # preferred source of mbids
    python -m scripts.run_extract_kworb         # fallback source of names
    python -m scripts.run_extract_musicbrainz
"""
import hashlib
import json
import time

import requests

from app.core.config import COUNTRIES, DATA_DIR
from app.services.extractors import musicbrainz

LASTFM_DIR = DATA_DIR / "raw" / "lastfm"
KWORB_DIR = DATA_DIR / "raw" / "kworb"
OUTPUT_DIR = DATA_DIR / "raw" / "musicbrainz"
CACHE_DIR = OUTPUT_DIR / "_artists"
PACING_SLEEP = 1.5  # seconds between HTTP calls; MB's limit is ~1 req/sec,
                     # padded since sitting right at 1.0 was still tripping 503s


def _cache_path(name: str):
    return CACHE_DIR / f"{hashlib.md5(name.encode('utf-8')).hexdigest()}.json"


def _cache_get(name: str) -> tuple[bool, list[dict] | None]:
    path = _cache_path(name)
    if not path.exists():
        return False, None
    payload = json.loads(path.read_text())
    return True, payload["genres"]


def _cache_put(name: str, genres: list[dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(name).write_text(json.dumps({"name": name, "genres": genres}))


def parse_artist_from_kworb_row(row_label: str) -> str:
    for sep in (" - ", "-"):
        if sep in row_label:
            return row_label.split(sep, 1)[0].strip()
    return row_label.strip()


def collect_candidates() -> dict[str, str | None]:
    """Returns {artist_name: mbid_or_None}, deduped, preferring Last.fm's mbid
    over a name found only via kworb."""
    candidates: dict[str, str | None] = {}

    for country in COUNTRIES:
        code = country["kworb_code"]

        lastfm_path = LASTFM_DIR / f"{code}.json"
        if lastfm_path.exists():
            payload = json.loads(lastfm_path.read_text())
            for a in payload.get("artists", []):
                name = a.get("name")
                if not name:
                    continue
                mbid = a.get("mbid") or None
                if name not in candidates or (mbid and not candidates[name]):
                    candidates[name] = mbid
            continue

        kworb_path = KWORB_DIR / f"{code}.json"
        if kworb_path.exists():
            payload = json.loads(kworb_path.read_text())
            for row in payload.get("rows", []):
                if len(row) < 3:
                    continue
                name = parse_artist_from_kworb_row(row[2])
                if name and name not in candidates:
                    candidates[name] = None

    return candidates


def resolve(name: str, mbid: str | None) -> list[dict]:
    """Returns the artist's genre list. Only writes to the cache on a
    request that actually completed - a network failure (timeout,
    connection error, or MusicBrainz still 503ing after retries) must NOT
    be cached as "no genres", or this artist would silently never be
    retried again. An empty-but-successful response (real MBID, genuinely
    no genre tags) is cached normally."""
    was_cached, cached = _cache_get(name)
    if was_cached:
        return cached

    if not mbid:
        try:
            mbid = musicbrainz.search_artist(name)
        except requests.RequestException as e:
            print(f"  search failed for {name}, will retry next run: {e}")
            return []
        time.sleep(PACING_SLEEP)
        if not mbid:
            _cache_put(name, [])  # a real "no match found" - safe to cache
            return []

    try:
        genres = musicbrainz.get_genres(mbid)
    except requests.RequestException as e:
        print(f"  genre lookup failed for {name}, will retry next run: {e}")
        return []
    time.sleep(PACING_SLEEP)

    _cache_put(name, genres)
    return genres


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    candidates = collect_candidates()
    print(f"[musicbrainz] {len(candidates)} unique artists to resolve")

    genres_by_name: dict[str, list[str]] = {}
    for i, (name, mbid) in enumerate(candidates.items(), 1):
        genre_dicts = resolve(name, mbid)
        genres_by_name[name] = [g["name"] for g in genre_dicts if g.get("name")]
        if i % 50 == 0:
            print(f"  {i}/{len(candidates)} artists done")

    for country in COUNTRIES:
        code = country["kworb_code"]
        name = country["lastfm_name"]

        artist_names: list[str] = []
        lastfm_path = LASTFM_DIR / f"{code}.json"
        kworb_path = KWORB_DIR / f"{code}.json"
        if lastfm_path.exists():
            payload = json.loads(lastfm_path.read_text())
            artist_names = [a["name"] for a in payload.get("artists", []) if a.get("name")]
        elif kworb_path.exists():
            payload = json.loads(kworb_path.read_text())
            seen: set[str] = set()
            for row in payload.get("rows", []):
                if len(row) < 3:
                    continue
                artist = parse_artist_from_kworb_row(row[2])
                if artist and artist not in seen:
                    seen.add(artist)
                    artist_names.append(artist)
        else:
            continue

        genres_by_artist = {a: genres_by_name.get(a, []) for a in artist_names}
        (OUTPUT_DIR / f"{code}.json").write_text(json.dumps({
            "country": name,
            "genres_by_artist": genres_by_artist,
        }, indent=2))

    resolved = sum(1 for g in genres_by_name.values() if g)
    print(f"[musicbrainz] {resolved}/{len(candidates)} artists had at least one genre")
    print(f"Done. Raw files in {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
