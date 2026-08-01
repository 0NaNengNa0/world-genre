"""Cleanse and reconcile raw extractor output.

This is the one place genre-taxonomy normalization, artist-name cleanup,
and cross-source reconciliation happen. The extractors are deliberately
dumb/raw (they save exactly what their API/scrape returned), and
app/services/countries.py is deliberately dumb/read-only (it just formats
already-cleansed data for the API) - this module is the actual "data
cleansing" step in between. See scripts/run_cleanse.py for the orchestration
that calls these functions and writes data/processed/{code}.json.

Pure functions, no file I/O - same convention as the extractors.

Genre matching is two-tiered:
  1. GENRE_ALIASES - exact-match dict for known *semantic* synonyms, where
     the raw text isn't textually similar to the canonical form at all
     ("rap" -> "hip-hop"; no fuzzy matcher would ever connect those strings).
  2. Fuzzy match against MusicBrainz's ~2,000-genre canonical list (from
     scripts/fetch_musicbrainz_genres.py) - catches *spelling* variants
     neither of us thought to hardcode ("chiptune" vs "chip tune", typos,
     etc). Only runs if that seed file has been fetched; otherwise this
     tier is silently skipped and normalize_genre() behaves as it did
     before (alias dict + lowercase only).
"""
import re
from collections import Counter

from rapidfuzz import fuzz, process

from app.core.config import MUSICBRAINZ_GENRES
from app.services.genre_buckets import bucket_genre

# Raw tags/genres are messy free text - "hip-hop", "Hip Hop", "rap", and
# "hiphop" all mean roughly the same thing to a listener, but a naive count
# would treat them as four different genres. Maps known variants to one
# canonical label. Not exhaustive - extend as you find more collisions.
GENRE_ALIASES = {
    "hip hop": "hip-hop",
    "hiphop": "hip-hop",
    "rap": "hip-hop",
    "trap": "hip-hop",
    "r&b": "rnb",
    "r & b": "rnb",
    "rhythm and blues": "rnb",
    "edm": "electronic",
    "dance": "electronic",
    "electronica": "electronic",
    "house": "electronic",
    "indie rock": "indie",
    "indie pop": "indie",
    "kpop": "k-pop",
    "k pop": "k-pop",
    "alternative rock": "alternative",
    "alt rock": "alternative",
}

# Splits a chart-row artist label on the first collaborator marker, so
# "Drake feat. Rihanna" or "A & B" resolve to one headline artist ("Drake",
# "A") instead of a mangled multi-artist string. Word-ish markers (feat/ft/
# featuring/with/&/x) require whitespace on both sides so "x" doesn't match
# mid-word (e.g. "Xzibit"); commas don't need that guard - a bare comma
# never appears inside an artist name, and real chart-row credits are far
# more often written "A, B, C" (no space before the comma) than "A , B" -
# a stricter comma rule used to silently fail to split that common case.
FEATURE_SPLIT_RE = re.compile(
    r"\s+(?:feat\.?|ft\.?|featuring|with|&|/|x)\s+|\s*,\s*", re.IGNORECASE
)

LASTFM_WEIGHT_DIVISOR = 20  # Last.fm tag counts run roughly 0-100

# Last.fm's crowd tags are noisy beyond just spelling - people tag artists
# with decades ("80s"), and other junk that isn't a genre at all. Filtering
# nationality tags or artist-name tags (also common) would need a reference
# list this project doesn't have; this catches the cheap, unambiguous cases.
_DECADE_RE = re.compile(r"^\d{2,4}s?$")


def _is_junk_tag(cleaned: str) -> bool:
    return bool(_DECADE_RE.match(cleaned)) or len(cleaned) <= 2

# Lowercased once at import time so every fuzzy-match call isn't re-lowering
# ~2,000 strings. Empty if fetch_musicbrainz_genres.py hasn't been run yet.
_CANONICAL_GENRES_LOWER = [g.lower() for g in MUSICBRAINZ_GENRES]
FUZZY_MATCH_THRESHOLD = 85  # 0-100; below this, treat as "no real match"


def _fuzzy_match_canonical(cleaned: str) -> str | None:
    if not _CANONICAL_GENRES_LOWER:
        return None
    result = process.extractOne(cleaned, _CANONICAL_GENRES_LOWER, scorer=fuzz.ratio)
    if result is None:
        return None
    match, score, _ = result
    return match if score >= FUZZY_MATCH_THRESHOLD else None


def normalize_genre(raw: str | None) -> str | None:
    """Lowercase, trim, and map to a canonical genre label.

    The alias dict only does semantic redirection ("rap" -> look up the
    "hip-hop" family) - it does NOT get final say on spelling. Whatever it
    resolves to still gets fuzzy-matched against MusicBrainz's canonical
    list, same as text that skipped the alias dict entirely. Without this,
    "hiphop" (-> alias "hip-hop") and a raw tag already spelled "hip-hop"
    (-> straight to fuzzy match, which finds MusicBrainz's actual spelling
    "hip hop") would land on two different final strings for one genre.

    Returns None for empty/junk input so callers can filter with a plain
    truthy check.
    """
    if not raw:
        return None
    cleaned = raw.strip().lower()
    if not cleaned or _is_junk_tag(cleaned):
        return None
    candidate = GENRE_ALIASES.get(cleaned, cleaned)
    return _fuzzy_match_canonical(candidate) or candidate


def normalize_artist_name(raw: str | None) -> str | None:
    """Strip whitespace and drop trailing collaborators from a raw chart-row
    label. Keeps the pipeline's one-artist-per-entry assumption honest
    instead of silently treating "A & B" as a single artist called "A & B"."""
    if not raw:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    primary = FEATURE_SPLIT_RE.split(cleaned, maxsplit=1)[0].strip()
    return primary or None


def merge_genre_signals(
    lastfm_tags_by_artist: dict[str, list[dict]],
    musicbrainz_genres_by_artist: dict[str, list[str]],
    top_n: int = 5,
    stats: dict | None = None,
) -> list[dict]:
    """Reconciles two independent genre signals into one ranked list -
    this is the real cross-source cleansing step, not just a fallback.

    Both sources vote into the same normalized-genre bucket. Last.fm's tags
    carry Last.fm's own 0-100 relevance score, so they're weighted by that;
    MusicBrainz genres are unweighted community votes, each worth 1. The
    `sources` field on each result is what makes "where do Last.fm and
    MusicBrainz agree or disagree about a country's genres" inspectable,
    instead of one source silently winning.

    Every genre also gets collapsed onto the <200-entry bucket taxonomy
    (app/services/genre_buckets.py) before scoring - so "chicago drill" and
    "trap" both count toward buckets meaningful enough to compare across
    20 countries, instead of staying 2,000+ fine-grained MusicBrainz labels
    that would never overlap between two countries at all.

    If `stats` is passed, it's mutated in place with
    {"total_tags", "unclassified_tags", "unclassified_rate"} - how many raw
    tags this call saw, and what fraction were junk/empty (normalize_genre
    returned None) or fell through to the "other" bucket. Without this, a
    genuine data-quality regression (e.g. a source starts returning garbage
    tags, or the bucket taxonomy stops covering what a country actually
    listens to) would be invisible - the pipeline would keep "succeeding"
    while silently discarding more and more signal. Optional and additive
    so existing callers that don't pass it see no behavior change.

    Returns [{"genre": str, "score": int, "sources": list[str]}, ...],
    highest score first.
    """
    scores: Counter[str] = Counter()
    sources: dict[str, set[str]] = {}
    total_tags = 0
    unclassified_tags = 0

    for tags in lastfm_tags_by_artist.values():
        for tag in tags[:5]:
            total_tags += 1
            raw_name = tag.get("name") if isinstance(tag, dict) else None
            name = normalize_genre(raw_name)
            if not name:
                unclassified_tags += 1
                continue
            name = bucket_genre(name)
            if name == "other":
                unclassified_tags += 1
                continue  # unclassifiable - shouldn't compete for a top-5 slot
            weight = 1
            try:
                weight = max(int(tag.get("count", 1)) // LASTFM_WEIGHT_DIVISOR, 1)
            except (TypeError, ValueError):
                pass
            scores[name] += weight
            sources.setdefault(name, set()).add("lastfm")

    for genres in musicbrainz_genres_by_artist.values():
        for raw_name in genres:
            total_tags += 1
            name = normalize_genre(raw_name)
            if not name:
                unclassified_tags += 1
                continue
            name = bucket_genre(name)
            if name == "other":
                unclassified_tags += 1
                continue
            scores[name] += 1
            sources.setdefault(name, set()).add("musicbrainz")

    if stats is not None:
        stats["total_tags"] = total_tags
        stats["unclassified_tags"] = unclassified_tags
        stats["unclassified_rate"] = (
            round(unclassified_tags / total_tags, 4) if total_tags else 0.0
        )

    return [
        {"genre": genre, "score": score, "sources": sorted(sources[genre])}
        for genre, score in scores.most_common(top_n)
    ]
