"""Look up artist metadata (image URL) from Deezer's public API.

No auth, no quota. Rate limit ~50 requests per 5 seconds — the module leaves
pacing/retry to the caller (the script), same convention as the other
extractors.
"""
import requests

API_BASE = "https://api.deezer.com"


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
