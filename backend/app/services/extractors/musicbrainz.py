"""Canonical artist metadata and genre tags from MusicBrainz.

Two sources, one set of parsers:

  - The **API** (`https://musicbrainz.org/ws/2`). Auth: none. Rate limit:
    ~1 request/second **per IP**, strictly enforced, and requests without a
    descriptive User-Agent (app name + contact) get blocked.
    See https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting
  - The **JSON data dumps**, one newline-delimited record per artist, "in the
    format returned by our JSON web service". Same payload shape, no rate
    limit, ~2.5M artists in one file.
    See https://musicbrainz.org/doc/Development/JSON_Data_Dumps

That equivalence is why the parsing below is split out as pure functions:
`parse_artist_meta` and `parse_genres` take a payload and know nothing about
where it came from. `scripts/run_import_mb_dump.py` feeds them dump lines;
`get_artist_meta` and `get_genres` feed them HTTP responses. Neither can drift
from the other, which matters because the two paths are meant to produce
identical rows.

It also makes the field logic testable without a network, which it previously
was not - the country fallback below is fiddly enough to deserve tests.

Two-step API lookup, because the search endpoint doesn't return genres:
  1. search_artist(name) -> best-match MBID
     (skip when you already have an mbid, e.g. from Last.fm's get_top_artists)
  2. get_genres(mbid) -> that artist's community-tagged genres

Pure functions, no file I/O, no pacing/sleep - that's the caller's job, same
convention as the other extractors. Network calls do retry themselves
(_get_with_retry) since musicbrainz.org is a shared community server and read
timeouts / 503s under its rate limiter are routine, not exceptional.
"""
import time

import requests

API_BASE = "https://musicbrainz.org/ws/2"
HEADERS = {"User-Agent": "world-genre-portfolio-project/1.0 (hatsuneneng@gmail.com)"}
MAX_RETRIES = 3


def parse_artist_meta(payload: dict) -> dict:
    """Origin country and formation year from an artist payload.

    `country` is preferred over `area` because MusicBrainz's area can be a
    city or region ("London") while country is the ISO 3166-1 code the rest
    of this project keys on. Falls back to the area's own country code when
    the top-level field is absent, which happens for a fair number of
    artists.

    Returns {"country": str|None, "formed_year": int|None} - both routinely
    None, since MusicBrainz's coverage of smaller artists is patchy. That's
    reported as coverage rather than hidden; see the domestic-share query.
    """
    country = payload.get("country")
    if not country:
        area = payload.get("area") or {}
        for code in area.get("iso-3166-1-codes") or []:
            country = code
            break

    begin = ((payload.get("life-span") or {}).get("begin")) or ""
    # Dates arrive as "1985", "1985-06" or "1985-06-21"; only the year is
    # meaningful for "how old is the music this country listens to".
    formed_year = int(begin[:4]) if begin[:4].isdigit() else None

    return {
        "country": country.lower() if country else None,
        "formed_year": formed_year,
    }


def parse_genres(payload: dict) -> list[dict]:
    """A lean genre list: name, count (community vote count).

    Often empty - MusicBrainz's genre coverage is patchier than Last.fm's
    tags, especially for less mainstream artists. That's expected; this is a
    secondary/cross-check source, not the primary one.
    """
    return [
        {"name": g.get("name"), "count": g.get("count")}
        for g in payload.get("genres", [])
    ]


def parse_aliases(payload: dict) -> list[str]:
    """Every name this artist is also known by, plus the primary name.

    Only present in dump records and in API responses asking for `inc=aliases`.
    This is the field that makes offline name matching work at all: the charts
    carry whatever spelling a streaming service used, and the alias list is
    MusicBrainz's record of which spellings mean the same artist.
    """
    names = [payload.get("name")]
    names.extend(a.get("name") for a in payload.get("aliases") or [])
    seen, ordered = set(), []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


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


def get_artist_meta(mbid: str, timeout: int = 20) -> dict:
    """One artist's origin country and formation year, over the API.

    The dump import resolves the same fields for every artist at once and
    should be preferred; this remains for the tail the dump misses and for
    artists that appeared since the last import.
    """
    resp = _get_with_retry(
        f"{API_BASE}/artist/{mbid}",
        params={"fmt": "json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return parse_artist_meta(resp.json())


def get_genres(mbid: str, timeout: int = 20) -> list[dict]:
    """One artist's community-tagged genres, over the API."""
    resp = _get_with_retry(
        f"{API_BASE}/artist/{mbid}",
        params={"inc": "genres", "fmt": "json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return parse_genres(resp.json())
