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
import math
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


def parse_artist_from_chart_row(row_label: str | None) -> str | None:
    """Pulls the artist out of a kworb chart-row label.

    kworb writes rows as "Artist - Song", but inconsistently: plenty use no
    spaces around the dash ("BTS-NORMAL", "ATEEZ-BAD"). Splitting only on
    " - " leaves those as one string, which then matches nothing in Deezer
    or MusicBrainz - that's where mangled entries like
    "Fuerza Regida-COQUETA(w/Grupo Frontera)" came from.

    Known limitation: with no spaces there's no way to tell "BTS-NORMAL"
    (artist-song) from a hyphenated artist name, so "Jay-Z-Song" yields
    "Jay". This path is only a fallback for countries Last.fm has no data
    for, and a truncated artist beats an unmatched one, but the real fix for
    an affected country is getting its Last.fm name right (see
    scripts/generate_countries_seed.py).
    """
    if not row_label:
        return None
    for separator in (" - ", "-"):
        if separator in row_label:
            return row_label.split(separator, 1)[0].strip() or None
    return row_label.strip() or None


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
    top_n: int | None = None,
    stats: dict | None = None,
    artists_by_genre: dict[str, set[str]] | None = None,
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
    countries, instead of staying 2,000+ fine-grained MusicBrainz labels
    that would never overlap between two countries at all.

    `top_n=None` (the default) returns the FULL ranked distribution. That
    matters: the genres that distinguish one country from another sit in
    the long tail, not the head - India's "bollywood" ranked 7th and
    Mexico's "reggaeton" 12th behind the same pop/rock/hip-hop every other
    country has. Truncating here would throw those away before
    scripts/run_cleanse.py can compute how *distinctive* each genre is,
    so callers that want a short list should rank first and truncate last.

    If `artists_by_genre` is passed, it's filled in with {genre: {artists}} -
    which artists actually caused each genre to score. This function is the
    only place that knows: it reads an artist's tags, normalizes and buckets
    them, adds to a running total and then moves on, so the link between
    "Aimyon" and "j-pop" existed only inside the loop. Capturing it here is
    what lets the API answer "who makes this country's j-pop" without
    recomputing the whole aggregation.

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

    for artist, tags in lastfm_tags_by_artist.items():
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
            if artists_by_genre is not None:
                artists_by_genre.setdefault(name, set()).add(artist)

    for artist, genres in musicbrainz_genres_by_artist.items():
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
            if artists_by_genre is not None:
                artists_by_genre.setdefault(name, set()).add(artist)

    if stats is not None:
        stats["total_tags"] = total_tags
        stats["unclassified_tags"] = unclassified_tags
        stats["unclassified_rate"] = (
            round(unclassified_tags / total_tags, 4) if total_tags else 0.0
        )

    return [
        {"genre": genre, "score": score, "sources": sorted(sources[genre])}
        for genre, score in scores.most_common(top_n)  # None => all, ranked
    ]


# Minimum share of a country's total genre weight (0-1) for a genre to count
# as genuinely present there.
#
# Both thresholds below are shares rather than raw scores, and that matters.
# An absolute floor silently changes meaning when the sample depth changes:
# at 20 artists per country a country's weights totalled ~250, at 100 they
# total ~1400, so a fixed floor of 3 went from roughly 1.2 percent of the
# total to 0.2 percent - it stopped filtering anything. Shares are stable
# across sample depth.
MIN_SHARE_PRESENT = 0.01  # 1 percent - below this, a genre is a rounding error
MIN_SHARE_FOR_DISTINCTIVENESS = 0.01


def _shares(rows: list[dict]) -> dict[str, float]:
    """Each genre's fraction (0-1) of one country's total genre weight."""
    total = sum(row["score"] for row in rows)
    if total <= 0:
        return {}
    return {row["genre"]: row["score"] / total for row in rows}


def genre_document_frequency(
    genres_by_country: dict[str, list[dict]],
    min_share: float = MIN_SHARE_PRESENT,
) -> Counter[str]:
    """How many countries each genre is *meaningfully* present in.

    The "document frequency" half of TF-IDF, where each country is a
    document. Counting bare presence does not survive a deep sample: with
    100 artists per country, one stray tag puts a genre in a country's
    distribution, so nearly every genre appears nearly everywhere and every
    IDF weight collapses toward zero. Observed directly - j-pop reached
    document frequency 20/20 and scored 0 distinctiveness for Japan while
    being 11 percent of what Japan actually plays.

    Requiring a genre to be at least `min_share` of a country's weight before
    that country counts restores the signal: one tagged artist no longer
    makes a genre "present" in a country nobody there listens to it in.
    """
    df: Counter[str] = Counter()
    for rows in genres_by_country.values():
        for genre, share in _shares(rows).items():
            if share >= min_share:
                df[genre] += 1
    return df


def score_distinctiveness(
    rows: list[dict],
    document_frequency: Counter[str],
    total_countries: int,
    min_share: float = MIN_SHARE_FOR_DISTINCTIVENESS,
) -> list[dict]:
    """Re-ranks one country's genres by how much they set it apart, rather
    than by raw popularity.

    Chart data measures commercial reach, so raw popularity says almost the
    same thing everywhere - across 20 countries, `pop` and `rock` appeared
    in all 20 and `pop` led 16 of them. Weighting each genre by inverse
    document frequency cancels exactly that shared baseline:

        distinctiveness = score * log(total_countries / countries_with_genre)

    A genre in every country scores log(1) = 0 and drops out; one in a
    single country gets the largest multiplier. So "avoid generic pop"
    needs no hardcoded artist or genre blocklist - the comparison across
    countries decides what counts as generic, and that stays true as the
    country list grows.

    `min_share` is a floor on how much of the country's own listening a
    genre has to account for before rarity can promote it. Rarity alone is
    not evidence: Japan had bossa nova at 0.4 percent of its weight ranked
    as its single most distinctive genre purely because few other countries
    registered it at all.

    Returns rows with a `distinctiveness` key added, highest first. Genres
    below the floor keep a distinctiveness of 0.0 rather than being dropped,
    so the caller still sees them in the full distribution.
    """
    shares = _shares(rows)
    scored = []
    for row in rows:
        df = document_frequency.get(row["genre"], 0)
        share = shares.get(row["genre"], 0.0)
        if df <= 0 or total_countries <= 0 or share < min_share:
            weight = 0.0
        else:
            weight = row["score"] * math.log(total_countries / df)
        scored.append({**row, "distinctiveness": round(weight, 3)})

    scored.sort(key=lambda r: r["distinctiveness"], reverse=True)
    return scored
