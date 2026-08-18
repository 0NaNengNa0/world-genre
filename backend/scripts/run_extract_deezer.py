"""Enrich artists with Deezer image URLs.

Reads unique artist names from data/raw/lastfm/*.json (or falls back to kworb),
looks each up on Deezer, caches results, and writes a single flat mapping:
    data/raw/deezer/artists.json  -> { "Artist Name": { "picture_medium": "...", ... } }

Run from the backend/ directory:
    python -m scripts.run_extract_lastfm      # source of truth for artist names
    python -m scripts.run_extract_deezer      # add images
"""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from app.core.config import COUNTRIES, DATA_DIR
from app.services.extractors import deezer

LASTFM_DIR = DATA_DIR / "raw" / "lastfm"
KWORB_DIR = DATA_DIR / "raw" / "kworb"
OUTPUT_DIR = DATA_DIR / "raw" / "deezer"
CACHE_DIR = OUTPUT_DIR / "_artists"
MAX_WORKERS = 5

# Bump whenever the matching rule changes, so cached answers chosen by the
# old rule are refetched instead of outliving it. Version 1 asked Deezer for
# a single result and kept it unconditionally, which cached a wrong artist
# for roughly a third of the catalogue - those entries have to be discarded
# rather than trusted, since nothing in them records how they were picked.
MATCH_VERSION = 2


def _cache_path(name: str):
    return CACHE_DIR / f"{hashlib.md5(name.encode('utf-8')).hexdigest()}.json"


def _cache_get(name: str) -> tuple[bool, dict | None]:
    path = _cache_path(name)
    if not path.exists():
        return False, None
    payload = json.loads(path.read_text())
    if payload.get("match_version") != MATCH_VERSION:
        return False, None
    return True, payload["artist"]


def _cache_put(name: str, artist: dict | None) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(name).write_text(
        json.dumps({"name": name, "artist": artist, "match_version": MATCH_VERSION})
    )


def _collect_unique_artists() -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for country in COUNTRIES:
        code = country["kworb_code"]

        lastfm_path = LASTFM_DIR / f"{code}.json"
        if lastfm_path.exists():
            payload = json.loads(lastfm_path.read_text())
            for a in payload.get("artists", []):
                name = a.get("name")
                if name and name not in seen:
                    seen.add(name)
                    ordered.append(name)
            continue

        kworb_path = KWORB_DIR / f"{code}.json"
        if kworb_path.exists():
            payload = json.loads(kworb_path.read_text())
            for row in payload.get("rows", []):
                if len(row) < 3:
                    continue
                label = row[2]
                artist = label.split("-", 1)[0].strip()
                if artist and artist not in seen:
                    seen.add(artist)
                    ordered.append(artist)
    return ordered


def _resolve(name: str) -> tuple[str, dict | None]:
    was_cached, cached = _cache_get(name)
    if was_cached:
        return name, cached
    try:
        artist = deezer.search_artist(name)
    except requests.HTTPError as e:
        print(f"  lookup failed for {name}: {e}")
        return name, None
    _cache_put(name, artist)
    return name, artist


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    artists = _collect_unique_artists()
    print(f"[deezer] {len(artists)} unique artists to resolve")

    result: dict[str, dict] = {}
    unmatched: list[str] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(_resolve, name) for name in artists]
        for i, future in enumerate(as_completed(futures), 1):
            name, artist = future.result()
            if not artist:
                unmatched.append(name)
            if artist:
                # Store None rather than Deezer's empty-hash placeholder URL,
                # so "has an image" is a real check downstream instead of a
                # truthiness test that always passes. See deezer.pick_picture.
                result[name] = {
                    "id": artist["id"],
                    "name": artist["name"],
                    # Comes back on the same lookup as the images, so keeping
                    # it costs nothing. It was previously dropped here, which
                    # meant a popularity signal already on disk (in the
                    # _artists cache) never reached the warehouse.
                    "nb_fan": artist.get("nb_fan"),
                    "picture_medium": (
                        artist.get("picture_medium")
                        if deezer.has_real_picture(artist.get("picture_medium"))
                        else None
                    ),
                    "picture_big": (
                        artist.get("picture_big")
                        if deezer.has_real_picture(artist.get("picture_big"))
                        else None
                    ),
                }
            if i % 100 == 0:
                print(f"  {i}/{len(artists)} done")

    (OUTPUT_DIR / "artists.json").write_text(json.dumps(result, indent=2))

    # Worth printing rather than hiding: an unmatched artist is a deliberate
    # refusal to guess, not a failure, and the list is the honest measure of
    # how far Deezer's catalogue reaches into non-Western charts.
    print(f"Done. {len(result)} matched -> {OUTPUT_DIR / 'artists.json'}")
    if unmatched:
        share = len(unmatched) / len(artists) * 100
        print(f"  {len(unmatched)} ({share:.0f} percent) had no confident match, e.g.:")
        for name in unmatched[:10]:
            print(f"    {name}")


if __name__ == "__main__":
    main()
