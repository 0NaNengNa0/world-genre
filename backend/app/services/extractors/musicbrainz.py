"""Look up canonical genre tags for artists via the MusicBrainz API.

Replaces the Spotify enrichment step (Spotify stopped exposing chart/genre
data to third-party dev-mode apps in Feb 2026 - see extractors/spotify.py,
now unused). MusicBrainz needs no account, no key, and has no daily quota,
just a strict rate limit and a required User-Agent.

Auth: none. Rate limit: ~1 request/second per IP, strictly enforced, and
requests without a descriptive User-Agent (app name + contact) get blocked.
See https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting

Two-step lookup, because the search endpoint doesn't return genres directly:
  1. search_artist(name) -> best-match MBID
     (skip this when you already have an mbid, e.g. from Last.fm's
     get_top_artists - go straight to get_genres)
  2. get_genres(mbid) -> that artist's community-tagged genres

Pure functions, no file I/O, no pacing/sleep - that's the caller's job,
same convention as the other extractors. Network calls do retry themselves
(_get_with_retry) since musicbrainz.org is a shared community server and
read timeouts / 503s under its rate limiter are routine, not exceptional.
"""
import time

import requests

API_BASE = "https://musicbrainz.org/ws/2"
HEADERS = {"User-Agent": "world-genre-portfolio-project/1.0 (hatsuneneng@gmail.com)"}
MAX_RETRIES = 3


def _get_with_retry(url: str, params: dict, timeout: int = 20) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            wait = 2 ** attempt
            print(f"  {e.__class__.__name__}, retrying in {wait}s "
                  f"(attempt {attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
            continue

        if resp.status_code == 503:  # MusicBrainz's rate-limit response
            # MusicBrainz sometimes sends "Retry-After: 0", which is not a
            # trustworthy signal - honoring it literally causes a tight
            # retry loop that just 503s again immediately. Floor it at 2s.
            header_wait = int(resp.headers.get("Retry-After", 0))
            wait = max(header_wait, 2 ** (attempt + 1))
            print(f"  503 rate-limited, waiting {wait}s (attempt {attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
            continue

        return resp

    raise last_exc or requests.ConnectionError(
        f"MusicBrainz request failed after {MAX_RETRIES} attempts: {url}"
    )


def search_artist(name: str, timeout: int = 20) -> str | None:
    """Returns the MBID of the best name match, or None if no result."""
    resp = _get_with_retry(
        f"{API_BASE}/artist/",
        params={"query": f'artist:"{name}"', "fmt": "json", "limit": 1},
        timeout=timeout,
    )
    resp.raise_for_status()
    artists = resp.json().get("artists", [])
    return artists[0]["id"] if artists else None


def get_genres(mbid: str, timeout: int = 20) -> list[dict]:
    """Returns a lean genre list: name, count (community vote count).

    Often empty - MusicBrainz's genre coverage is patchier than Last.fm's
    tags, especially for less mainstream artists. That's expected; this is
    a secondary/cross-check source, not the primary one.
    """
    resp = _get_with_retry(
        f"{API_BASE}/artist/{mbid}",
        params={"inc": "genres", "fmt": "json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    genres = resp.json().get("genres", [])
    return [{"name": g.get("name"), "count": g.get("count")} for g in genres]
