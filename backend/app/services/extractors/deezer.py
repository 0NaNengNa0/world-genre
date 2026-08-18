"""Look up artist metadata (image URL) from Deezer's public API.

No auth, no quota. Rate limit ~50 requests per 5 seconds — the module leaves
pacing/retry to the caller (the script), same convention as the other
extractors.
"""
import re
import unicodedata

import requests

API_BASE = "https://api.deezer.com"

# How many candidates to weigh before giving up on a name. Deezer's relevance
# ranking regularly puts an impostor first, so asking for one result and
# trusting it is what produced "Nirvana" -> "Nirvana (UK)" with 170 fans.
SEARCH_CANDIDATES = 10

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


def normalize_name(name: str) -> str:
    """Casefold and strip accents, but deliberately keep punctuation.

    Accents must be ignored because Deezer's spelling is often the correct
    one for the same act: "Babasonicos" -> "Babasónicos" (411k fans) and
    "Seru Giran" -> "Serú Girán" (50k) are both real matches that a strict
    comparison would throw away.

    Punctuation must be kept for the opposite reason. The impostors that
    outrank real artists are punctuation variants - "Young T.H.U.G.",
    "T.W.I.C.E.", "Duki.", "Jul!" - so stripping dots would make every one
    of them compare equal to the artist it is impersonating. Keeping
    punctuation is what separates the two groups.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped).strip().casefold()


def pick_best_match(query: str, candidates: list[dict]) -> dict | None:
    """The most-followed artist whose name actually equals `query`, or None.

    Two rules, both learned from the data:

    1. Only exact (accent-insensitive) name matches are eligible. Deezer
       returns a nearest-neighbour rather than nothing, so a fuzzy hit means
       "no such artist here", not "close enough" - 67 of 754 lookups came
       back under a name we never asked for.

    2. Among exact matches, take the highest fan count. Several distinct
       artists share a name exactly ("Drake", "Miguel", "Jao"), and the one
       we want is the one on a national streaming chart, which is always the
       most-followed of them by a wide margin.

    Returning None is a real answer. Every artist here charts in a Spotify
    top 200, so no plausible match means the lookup failed, and recording
    that is better than attaching a stranger's fan count and photograph.
    """
    target = normalize_name(query)
    exact = [c for c in candidates if normalize_name(c.get("name") or "") == target]
    if not exact:
        return None
    return max(exact, key=lambda c: c.get("nb_fan") or 0)


def search_artist(name: str, timeout: int = 15) -> dict | None:
    """Best confident artist match for `name`, or None if there isn't one.

    Response fields we care about: id, name, nb_fan, picture_medium,
    picture_big.
    """
    resp = requests.get(
        f"{API_BASE}/search/artist",
        params={"q": name, "limit": SEARCH_CANDIDATES},
        timeout=timeout,
    )
    resp.raise_for_status()
    return pick_best_match(name, resp.json().get("data", []))
