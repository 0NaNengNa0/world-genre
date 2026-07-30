"""Collapse MusicBrainz's ~2,200 fine-grained genres onto a curated set of
under 200 broad genre buckets (seeds/genre_buckets.txt).

Why this exists: "chicago drill", "uk drill", "trap", and "cloud rap" are
all meaningfully hip-hop for a "which genres does this country listen to"
comparison - keeping them as 2,000+ distinct labels makes cross-country
genre comparison meaningless (nothing lines up). This is a second,
coarser normalization pass on top of cleansing.normalize_genre()'s
spelling fixes, not a replacement for it.

Matching, in priority order:
  1. Exact match - the input already IS a bucket name.
  2. Substring match - a bucket name appears inside the input, longest
     bucket names checked first ("death metal" before generic "metal", so
     "death metal" doesn't get flattened to the broader bucket needlessly).
  3. Keyword rules - broad catch-all substrings for genre families that
     don't have every variant spelled out in the bucket list itself
     (e.g. anything with "hous" or "techno" in it -> "electronic").
  4. Fuzzy match against the bucket list, for spelling drift.
  5. "other" - the honest fallback for anything genuinely unclassifiable
     (hyper-regional/traditional genres mostly end up here, which is
     expected - flattening those into a "world" bucket would erase more
     information than it'd add for a country-comparison dashboard).
"""
from rapidfuzz import fuzz, process

from app.core.config import GENRE_BUCKETS

_BUCKETS_LOWER = [b.lower() for b in GENRE_BUCKETS]
_BUCKET_SET = set(_BUCKETS_LOWER)
# Longest-first so "death metal" matches before the shorter "metal" does.
_BUCKETS_BY_LENGTH_DESC = sorted(_BUCKETS_LOWER, key=len, reverse=True)

FUZZY_MATCH_THRESHOLD = 88  # stricter than cleansing.py's - false merges here
                            # are more visible (the whole point is grouping)

# Broad substring rules for genre families where spelling it every variant
# out in genre_buckets.txt would be endless. Order matters within reason,
# but each rule's target bucket is specific enough that overlap is rare.
KEYWORD_RULES: list[tuple[str, str]] = [
    ("hip hop", "hip-hop"), ("hiphop", "hip-hop"), ("rap", "hip-hop"),
    ("trap", "trap"), ("drill", "drill"), ("grime", "grime"),
    ("r&b", "r&b"), ("rnb", "r&b"), ("soul", "soul"), ("funk", "funk"),
    ("hous", "electronic"), ("techno", "electronic"), ("trance", "electronic"),
    ("dubstep", "electronic"), ("garage", "electronic"), ("jungle", "electronic"),
    ("breakbeat", "electronic"), ("electro", "electronic"), ("synth", "electronic"),
    ("edm", "electronic"), ("bass", "electronic"), ("core", "electronic"),
    ("metal", "metal"), ("punk", "punk"), ("emo", "emo"),
    ("rock", "rock"), ("grunge", "grunge"),
    ("jazz", "jazz"), ("blues", "blues"), ("country", "country"),
    ("bluegrass", "bluegrass"), ("folk", "folk"), ("americana", "americana"),
    ("classical", "classical"), ("baroque", "baroque"), ("opera", "opera"),
    ("symphon", "classical"), ("chant", "classical"), ("cantata", "classical"),
    ("sonata", "classical"), ("concerto", "classical"), ("choral", "classical"),
    ("reggae", "reggae"), ("dancehall", "dancehall"), ("ska", "ska"), ("dub", "dub"),
    ("soca", "soca"), ("calypso", "calypso"), ("reggaeton", "reggaeton"),
    ("salsa", "salsa"), ("merengue", "merengue"), ("bachata", "bachata"),
    ("cumbia", "cumbia"), ("tango", "tango"), ("samba", "samba"),
    ("flamenco", "flamenco"), ("mariachi", "mariachi"), ("ranchera", "ranchera"),
    ("latin", "latin"), ("afrobeat", "afrobeat"), ("afro", "afrobeats"),
    ("highlife", "highlife"), ("amapiano", "amapiano"), ("kwaito", "kwaito"),
    ("gospel", "gospel"), ("christian", "christian"), ("worship", "worship"),
    ("disco", "disco"), ("gamelan", "gamelan"), ("bollywood", "bollywood"),
    ("bhangra", "bhangra"), ("qawwali", "qawwali"), ("carnatic", "carnatic classical"),
    ("hindustani", "hindustani classical"), ("k-pop", "k-pop"), ("kpop", "k-pop"),
    ("j-pop", "j-pop"), ("jpop", "j-pop"), ("c-pop", "c-pop"), ("mandopop", "mandopop"),
    ("cantopop", "cantopop"), ("pop", "pop"),
    ("ambient", "ambient"), ("new age", "new age"), ("noise", "noise"),
    ("experimental", "experimental"), ("avant-garde", "avant-garde"),
    ("chant", "classical"), ("comedy", "comedy"), ("spoken word", "spoken word"),
    ("poetry", "poetry"), ("soundtrack", "soundtrack"), ("musical", "musical theatre"),
    ("children", "children's music"), ("video game", "video game music"),
]


def _fuzzy_match_bucket(cleaned: str) -> str | None:
    if not _BUCKETS_LOWER:
        return None
    result = process.extractOne(cleaned, _BUCKETS_LOWER, scorer=fuzz.ratio)
    if result is None:
        return None
    match, score, _ = result
    return match if score >= FUZZY_MATCH_THRESHOLD else None


def bucket_genre(genre: str | None) -> str:
    """Maps a (spelling-normalized) genre string onto one of the <200
    broad buckets in seeds/genre_buckets.txt. Always returns a string -
    "other" rather than None, since every genre needs SOME bucket for
    aggregation to make sense."""
    if not genre:
        return "other"
    cleaned = genre.strip().lower()
    if not cleaned:
        return "other"

    if cleaned in _BUCKET_SET:
        return cleaned

    for bucket in _BUCKETS_BY_LENGTH_DESC:
        if bucket in cleaned:
            return bucket

    for keyword, bucket in KEYWORD_RULES:
        if keyword in cleaned:
            return bucket

    fuzzy = _fuzzy_match_bucket(cleaned)
    if fuzzy:
        return fuzzy

    return "other"
