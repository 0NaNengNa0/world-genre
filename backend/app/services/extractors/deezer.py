"""Look up artist metadata (image URL) from Deezer's public API.

No auth, no quota. Rate limit ~50 requests per 5 seconds — the module leaves
pacing/retry to the caller (the script), same convention as the other
extractors.
"""
import requests

API_BASE = "https://api.deezer.com"

# Deezer never returns an empty picture field - for artists it has no photo
# for, it returns a normal-looking CDN URL whose path segment is the MD5 of
# the empty string. So a truthiness check on picture_medium passes and the
# frontend then renders a blank grey square. Affects real, well-known artists
# (Radiohead, Coldplay, The Weeknd), so it can't be dismissed as long-tail.
_EMPTY_IMAGE_HASH = "d41d8cd98f00b204e9800998ecf8427e"


def has_real_picture(url: str | None) -> bool:
    """False for missing URLs and for Deezer's empty-hash placeholder."""
    return bool(url) and _EMPTY_IMAGE_HASH not in url


def pick_picture(artist: dict) -> str | None:
    """Best available real picture URL for an artist payload, or None.

    Prefers `picture_medium`; falls back to `picture_big` because the
    placeholder is per-size, so an artist can genuinely have one and not the
    other.
    """
    for field in ("picture_medium", "picture_big"):
        url = artist.get(field)
        if has_real_picture(url):
            return url
    return None


def search_artist(name: str, timeout: int = 15) -> dict | None:
    """Returns Deezer's first artist match, or None if no result.

    Response fields we care about: id, name, picture, picture_medium,
    picture_big, picture_xl.
    """
    resp = requests.get(
        f"{API_BASE}/search/artist",
        params={"q": name, "limit": 1},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    return data[0] if data else None
