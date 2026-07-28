"""
Extract country-level Top 50 chart data + artist genres from the Spotify Web API.

Auth: Client Credentials flow (app-only, no user login needed) - works with
your Premium account's developer app, and is enough for public catalog data
(playlists, tracks, artists). Get SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET
from https://developer.spotify.com/dashboard (Create app, then Settings).

Output: one raw JSON file per country under raw/spotify/{country}.json
"""
import base64
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from countries import COUNTRIES

load_dotenv()

CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
OUTPUT_DIR = Path("raw/spotify")


def get_access_token() -> str:
    """Client Credentials flow: exchange client id/secret for an app token."""
    auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        TOKEN_URL,
        headers={"Authorization": f"Basic {auth_header}"},
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def find_top50_playlist_id(token: str, spotify_q: str) -> str | None:
    """Search for the official Spotify-owned 'Top 50 - {Country}' playlist."""
    resp = requests.get(
        f"{API_BASE}/search",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": spotify_q, "type": "playlist", "limit": 5},
        timeout=15,
    )
    resp.raise_for_status()
    items = resp.json().get("playlists", {}).get("items", [])
    for item in items:
        if item and item.get("owner", {}).get("id") == "spotify":
            return item["id"]
    # Fallback: just take the first result if no exact Spotify-owned match
    return items[0]["id"] if items else None


def get_playlist_tracks(token: str, playlist_id: str) -> list[dict]:
    resp = requests.get(
        f"{API_BASE}/playlists/{playlist_id}/tracks",
        headers={"Authorization": f"Bearer {token}"},
        params={"fields": "items(track(id,name,artists(id,name)))", "limit": 50},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


def get_artist_genres(token: str, artist_ids: list[str]) -> dict[str, list[str]]:
    """Batch-fetch genres for up to 50 artist IDs per call."""
    genres_by_artist = {}
    for i in range(0, len(artist_ids), 50):
        batch = artist_ids[i : i + 50]
        resp = requests.get(
            f"{API_BASE}/artists",
            headers={"Authorization": f"Bearer {token}"},
            params={"ids": ",".join(batch)},
            timeout=15,
        )
        resp.raise_for_status()
        for artist in resp.json().get("artists", []):
            if artist:
                genres_by_artist[artist["id"]] = artist.get("genres", [])
    return genres_by_artist


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    token = get_access_token()

    for country in COUNTRIES:
        name, spotify_q = country["lastfm_name"], country["spotify_q"]
        print(f"[spotify] {name} ...")

        playlist_id = find_top50_playlist_id(token, spotify_q)
        if not playlist_id:
            print(f"  no playlist found for '{spotify_q}', skipping")
            continue

        tracks = get_playlist_tracks(token, playlist_id)
        artist_ids = sorted({
            a["id"] for item in tracks if item.get("track")
            for a in item["track"].get("artists", [])
        })
        genres_by_artist = get_artist_genres(token, artist_ids)

        out_path = OUTPUT_DIR / f"{country['kworb_code']}.json"
        out_path.write_text(json.dumps({
            "country": name,
            "playlist_id": playlist_id,
            "tracks": tracks,
            "genres_by_artist": genres_by_artist,
        }, indent=2))

        time.sleep(0.5)  # be polite to the API

    print(f"Done. Raw files in {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
