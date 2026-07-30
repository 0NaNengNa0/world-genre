"""Extract top artists per country + genre tags per artist from Last.fm.

Pipeline:
1. Fetch top artists per country (sequential; one call per country).
2. Deduplicate artist names across all countries.
3. Fetch tags per unique artist concurrently (ThreadPoolExecutor).
4. Write one JSON per country, with tags looked up from the shared cache.

Run from the backend/ directory:
    python -m scripts.run_extract_lastfm
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv

from app.core.config import COUNTRIES, DATA_DIR
from app.services.extractors import lastfm

OUTPUT_DIR = DATA_DIR / "raw" / "lastfm"
MAX_WORKERS = 5  # Last.fm asks for ~5 req/sec max


def fetch_tags_safe(api_key: str, artist: str) -> tuple[str, list[dict]]:
    try:
        return artist, lastfm.get_top_tags(api_key, artist)
    except requests.HTTPError as e:
        print(f"  tag lookup failed for {artist}: {e}")
        return artist, []


def main() -> None:
    load_dotenv()
    api_key = os.environ["LASTFM_API_KEY"]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    artists_by_country: dict[str, list[dict]] = {}
    unique_artists: set[str] = set()

    for country in COUNTRIES:
        api_name, code = country["lastfm_name"], country["kworb_code"]
        print(f"[lastfm] fetching top artists for {country['country_name']} ...")
        # countries.py only ever surfaces the top 5 genres/artists per country,
        # so fetching 50 candidates per country was mostly wasted downstream
        # work - it's what was driving MusicBrainz's ~300-artist queue at
        # ~1 req/sec. 20 still gives plenty of headroom for genre aggregation
        # while roughly halving the MusicBrainz backlog.
        artists = lastfm.get_top_artists(api_key, api_name, limit=20)
        artists_by_country[code] = artists
        unique_artists.update(a["name"] for a in artists)

    print(f"[lastfm] {len(unique_artists)} unique artists across all countries")
    print(f"[lastfm] fetching tags with {MAX_WORKERS} workers ...")

    tags_cache: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(fetch_tags_safe, api_key, a) for a in unique_artists]
        for i, future in enumerate(as_completed(futures), 1):
            artist, tags = future.result()
            tags_cache[artist] = tags
            if i % 50 == 0:
                print(f"  {i}/{len(unique_artists)} artists done")

    for country in COUNTRIES:
        code, display_name = country["kworb_code"], country["country_name"]
        artists = artists_by_country[code]
        tags_by_artist = {a["name"]: tags_cache.get(a["name"], []) for a in artists}
        (OUTPUT_DIR / f"{code}.json").write_text(json.dumps({
            "country": display_name,
            "artists": artists,
            "tags_by_artist": tags_by_artist,
        }, indent=2))

    print(f"Done. Raw files in {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
