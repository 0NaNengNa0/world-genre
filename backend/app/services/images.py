"""Artist photo lookup, merged from Deezer and Wikidata.

Both sources are needed because each fails where the other works. Deezer
covers current chart artists far better; Wikimedia Commons requires a free
licence, so its coverage skews older and more Western and misses most K-pop
and J-pop. Deezer therefore wins wherever it has a real photo, and Wikidata
fills the gaps.

This used to live in the API service and be consulted per request. It now runs
in the publish step instead, so the cover image is baked into the published
payload and the API never touches these files at all.
"""
import json
import logging
import time

from app.core.config import DATA_DIR
from app.services.extractors.deezer import pick_picture

logger = logging.getLogger(__name__)

DEEZER_ARTISTS_PATH = DATA_DIR / "raw" / "deezer" / "artists.json"
WIKIDATA_ARTISTS_PATH = DATA_DIR / "raw" / "wikidata" / "artists.json"

_cache: tuple[tuple[float, float], dict[str, str]] | None = None
_checked_at: float = 0.0

# The files are rewritten once per pipeline run, so re-checking more often
# than this buys nothing. Against a bucket each check is an HTTP round trip.
CACHE_TTL_SECONDS = 60.0


def reset_cache() -> None:
    global _cache, _checked_at
    _cache, _checked_at = None, 0.0


def _mtime(path) -> float:
    """Modification time, or 0.0 if unreadable.

    Catches Exception rather than OSError because this also runs against
    cloud paths, and cloudpathlib raises its own types for a missing object or
    a denied request. A stale-but-served mapping beats an exception.
    """
    try:
        return path.stat().st_mtime
    except Exception:
        return 0.0


def _load_deezer() -> dict[str, str]:
    if not DEEZER_ARTISTS_PATH.exists():
        return {}
    payload = json.loads(DEEZER_ARTISTS_PATH.read_text())
    images = {}
    for name, data in payload.items():
        if not isinstance(data, dict):
            continue
        # pick_picture, not `data["picture_medium"]`: Deezer returns a
        # well-formed URL whose path is the MD5 of the empty string for
        # artists it has no photo of, so a truthiness check passes and the
        # browser renders a blank square.
        url = pick_picture(data)
        if url:
            images[name] = url
    return images


def _load_wikidata() -> dict[str, str]:
    if not WIKIDATA_ARTISTS_PATH.exists():
        return {}
    payload = json.loads(WIKIDATA_ARTISTS_PATH.read_text())
    return {
        name: data["image"]
        for name, data in payload.items()
        if isinstance(data, dict) and data.get("image")
    }


def artist_images() -> dict[str, str]:
    """Merged artist-to-photo mapping, Deezer preferred."""
    global _cache, _checked_at

    now = time.monotonic()
    if _cache is not None and now - _checked_at < CACHE_TTL_SECONDS:
        return _cache[1]

    stamps = (_mtime(DEEZER_ARTISTS_PATH), _mtime(WIKIDATA_ARTISTS_PATH))
    _checked_at = now
    if _cache is None or _cache[0] != stamps:
        merged = _load_wikidata()
        merged.update(_load_deezer())  # Deezer takes precedence
        _cache = (stamps, merged)
    return _cache[1]


def cover_image(artists: list[str]) -> str | None:
    """First artist in the list that actually has a photo.

    Scans rather than taking artists[0] because roughly 7 percent have no
    usable image, and taking the first unconditionally would show a
    placeholder whenever the top artist happens to be one of them.
    """
    images = artist_images()
    for name in artists:
        url = images.get(name)
        if url:
            return url
    return None
