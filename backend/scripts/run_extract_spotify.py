"""Enrich kworb chart artists with Spotify metadata (genres, when present).

Pipeline:
1. Read data/raw/kworb/{code}.json for each country.
2. Deduplicate artist names across all countries.
3. Sequentially look up each artist on Spotify (dev-mode friendly).
4. Cache each successful/empty result to disk so reruns skip completed work.
5. Assemble per-country output.

Rate-limit strategy:
- Serial (no threadpool). Concurrency doesn't help since Spotify's dev-mode
  budget is a shared rolling window.
- Small pacing sleep between calls.
- Circuit breaker: too many consecutive failures -> abort so we don't waste
  the rest of the daily quota.

Run from the backend/ directory:
    python -m scripts.run_extract_kworb        # produce the kworb source first
    python -m scripts.run_extract_spotify
"""
import hashlib
import json
import os
import time

import requests
from dotenv import load_dotenv

from app.core.config import COUNTRIES, DATA_DIR
from app.services.extractors import spotify

KWORB_DIR = DATA_DIR / "raw" / "kworb"
OUTPUT_DIR = DATA_DIR / "raw" / "spotify"
CACHE_DIR = OUTPUT_DIR / "_artists"
PACING_SLEEP = 0.4                 # seconds between API calls
CONSECUTIVE_FAILURE_LIMIT = 15     # abort if this many 429s in a row


def _cache_path(name: str) -> "os.PathLike":
    return CACHE_DIR / f"{hashlib.md5(name.encode('utf-8')).hexdigest()}.json"


def _cache_get(name: str) -> tuple[bool, dict | None]:
    """Returns (was_cached, artist_or_None)."""
    path = _cache_path(name)
    if not path.exists():
        return False, None
    payload = json.loads(path.read_text())
    return True, payload["artist"]


def _cache_put(name: str, artist: dict | None) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(name).write_text(json.dumps({"name": name, "artist": artist}))


def parse_artist(row_label: str) -> str:
    for sep in (" - ", "-"):
        if sep in row_label:
            return row_label.split(sep, 1)[0].strip()
    return row_label.strip()


def artists_from_kworb(payload: dict) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for row in payload.get("rows", []):
        if len(row) < 3:
            continue
        artist = parse_artist(row[2])
        if artist and artist not in seen:
            seen.add(artist)
            ordered.append(artist)
    return ordered


def main() -> None:
    load_dotenv()
    token = spotify.get_access_token(
        os.environ["SPOTIFY_CLIENT_ID"],
        os.environ["SPOTIFY_CLIENT_SECRET"],
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    artists_by_country: dict[str, list[str]] = {}
    unique_artists: list[str] = []
    seen: set[str] = set()

    for country in COUNTRIES:
        name, code = country["lastfm_name"], country["kworb_code"]
        kworb_path = KWORB_DIR / f"{code}.json"
        if not kworb_path.exists():
            print(f"[spotify] {name}: no kworb data, skipping")
            continue
        payload = json.loads(kworb_path.read_text())
        artists = artists_from_kworb(payload)
        artists_by_country[name] = artists
        for a in artists:
            if a not in seen:
                seen.add(a)
                unique_artists.append(a)

    print(f"[spotify] {len(unique_artists)} unique artists across all countries")

    cache: dict[str, dict | None] = {}
    consecutive_failures = 0
    api_calls = 0
    cache_hits = 0

    for i, name in enumerate(unique_artists, 1):
        was_cached, artist = _cache_get(name)
        if was_cached:
            cache[name] = artist
            cache_hits += 1
            continue

        try:
            artist = spotify.search_artist(token, name)
            _cache_put(name, artist)
            cache[name] = artist
            api_calls += 1
            consecutive_failures = 0
            time.sleep(PACING_SLEEP)
        except spotify.QuotaExceeded:
            print("  daily quota exhausted; aborting. Rerun tomorrow to continue.")
            break
        except requests.HTTPError as e:
            consecutive_failures += 1
            print(f"  lookup failed for {name}: {e}")
            cache[name] = None
            if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                print(f"  {consecutive_failures} consecutive failures; aborting")
                break

        if i % 50 == 0:
            print(f"  {i}/{len(unique_artists)} artists done "
                  f"(api={api_calls}, cache={cache_hits})")

    print(f"[spotify] resolved {sum(1 for v in cache.values() if v)} artists "
          f"({api_calls} API calls, {cache_hits} cache hits)")

    for country in COUNTRIES:
        name, code = country["lastfm_name"], country["kworb_code"]
        if name not in artists_by_country:
            continue
        artist_ids: dict[str, str] = {}
        genres_by_artist: dict[str, list[str]] = {}
        for artist in artists_by_country[name]:
            data = cache.get(artist)
            if data:
                artist_ids[artist] = data["id"]
                genres_by_artist[artist] = data.get("genres", [])
        (OUTPUT_DIR / f"{code}.json").write_text(json.dumps({
            "country": name,
            "artist_ids": artist_ids,
            "genres_by_artist": genres_by_artist,
        }, indent=2))

    print(f"Done. Raw files in {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
