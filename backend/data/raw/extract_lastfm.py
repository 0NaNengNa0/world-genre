"""
Extract top artists per country + crowd-sourced genre tags from the Last.fm API.

Auth: just an API key, no OAuth. Get one free at
https://www.last.fm/api/account/create (issued instantly).

Output: one raw JSON file per country under raw/lastfm/{country}.json
"""
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from countries import COUNTRIES

load_dotenv()

API_KEY = os.environ["LASTFM_API_KEY"]
BASE_URL = "http://ws.audioscrobbler.com/2.0/"
OUTPUT_DIR = Path("raw/lastfm")


def call(method: str, **params) -> dict:
    resp = requests.get(
        BASE_URL,
        params={"method": method, "api_key": API_KEY, "format": "json", **params},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_top_artists(country_name: str, limit: int = 50) -> list[dict]:
    data = call("geo.gettopartists", country=country_name, limit=limit)
    return data.get("topartists", {}).get("artist", [])


def get_top_tags(artist_name: str) -> list[dict]:
    data = call("artist.gettoptags", artist=artist_name)
    return data.get("toptags", {}).get("tag", [])


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for country in COUNTRIES:
        name, code = country["lastfm_name"], country["kworb_code"]
        print(f"[lastfm] {name} ...")

        artists = get_top_artists(name)
        tags_by_artist = {}
        for artist in artists:
            artist_name = artist["name"]
            try:
                tags_by_artist[artist_name] = get_top_tags(artist_name)
            except requests.HTTPError as e:
                print(f"  tag lookup failed for {artist_name}: {e}")
                tags_by_artist[artist_name] = []
            time.sleep(0.2)  # Last.fm asks for ~5 req/sec max; stay well under

        out_path = OUTPUT_DIR / f"{code}.json"
        out_path.write_text(json.dumps({
            "country": name,
            "artists": artists,
            "tags_by_artist": tags_by_artist,
        }, indent=2))

    print(f"Done. Raw files in {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
