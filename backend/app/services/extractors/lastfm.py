"""Extract top artists per country and their genre tags from the Last.fm API.

Auth: API key only, no OAuth. Get one at https://www.last.fm/api/account/create.

Pure functions. Callers pass in the api_key and get back Python data.
"""
import requests

BASE_URL = "http://ws.audioscrobbler.com/2.0/"


def _call(api_key: str, method: str, timeout: int = 15, **params) -> dict:
    resp = requests.get(
        BASE_URL,
        params={"method": method, "api_key": api_key, "format": "json", **params},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def get_top_artists(api_key: str, country_name: str, limit: int = 50) -> list[dict]:
    """Returns a lean artist list: name, listeners, mbid.

    Last.fm's `image` field has returned an identical placeholder for every
    artist since 2019 — strip it. `streamable` is always 0, also dropped.
    `@attr.rank` is redundant with array order.
    """
    data = _call(api_key, "geo.gettopartists", country=country_name, limit=limit)
    return [
        {
            "name": a.get("name"),
            "listeners": a.get("listeners"),
            "mbid": a.get("mbid"),
        }
        for a in data.get("topartists", {}).get("artist", [])
    ]


def get_top_tags(api_key: str, artist_name: str) -> list[dict]:
    """Returns a lean tag list: name, count. Drops the `url` field."""
    data = _call(api_key, "artist.gettoptags", artist=artist_name)
    return [
        {"name": t.get("name"), "count": t.get("count")}
        for t in data.get("toptags", {}).get("tag", [])
    ]
