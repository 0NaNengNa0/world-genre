"""Extract top artists per country and their genre tags from the Last.fm API.

Auth: API key only, no OAuth. Get one at https://www.last.fm/api/account/create.

Pure functions. Callers pass in the api_key and get back Python data.
"""
import re

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


def get_tag_info(api_key: str, tag: str) -> dict | None:
    """A genre's short description, or None if Last.fm has no wiki for it.

    Uses tag.getInfo, which returns a short "summary" and a longer "content".
    Only the summary is kept - the content runs to several paragraphs and this
    is a caption, not an article.

    The summary carries a trailing "Read more on Last.fm" link in HTML; it's
    stripped here rather than in the UI, so the stored text is clean whatever
    consumes it. Returns None (not an empty dict) when there's no wiki at all,
    which is common for narrower genres.
    """
    data = _call(api_key, "tag.getinfo", tag=tag)
    wiki = (data.get("tag") or {}).get("wiki") or {}
    summary = (wiki.get("summary") or "").strip()
    if not summary:
        return None

    # The summary is HTML: prose followed by <a href="...">Read more on
    # Last.fm</a>. Cutting at the anchor leaves the sentence intact.
    text = re.split(r"<a\s", summary, maxsplit=1)[0]
    text = re.sub(r"<[^>]+>", "", text).strip()
    # Last.fm ends the truncated summary with a bare ellipsis or spaces.
    text = text.rstrip(" .\n") + "." if text else ""
    if not text:
        return None

    return {
        "summary": text,
        "url": (data.get("tag") or {}).get("url"),
    }


def get_top_tags(api_key: str, artist_name: str) -> list[dict]:
    """Returns a lean tag list: name, count. Drops the `url` field."""
    data = _call(api_key, "artist.gettoptags", artist=artist_name)
    return [
        {"name": t.get("name"), "count": t.get("count")}
        for t in data.get("toptags", {}).get("tag", [])
    ]
