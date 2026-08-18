"""Shared configuration and seed loaders.

Keeps a single place to answer: where do settings and reference data come from?
Extractors, scripts, and API routes all import from here so a change to the
countries list or an env var only touches one file.
"""
import csv
from pathlib import Path

from app.core.storage import data_dir_from_env

BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Source files that ship inside the image. These must stay local paths even
# when data moves to a bucket - they're code and reference data, versioned in
# git, not pipeline output. SQL_DIR exists because the query files used to be
# reached via DATA_DIR.parent, which quietly pointed them at the bucket root
# the moment DATA_DIR became a gs:// URL.
SEEDS_DIR = BACKEND_ROOT / "seeds"
SQL_DIR = BACKEND_ROOT / "sql"

# Pipeline output: local by default, a GCS bucket when DATA_DIR is set.
# See app/core/storage.py.
DATA_DIR = data_dir_from_env(BACKEND_ROOT / "data")


def load_countries() -> list[dict]:
    with (SEEDS_DIR / "countries.csv").open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_musicbrainz_genres() -> list[str]:
    """Canonical genre list from scripts/fetch_musicbrainz_genres.py. Returns
    an empty list if that script hasn't been run yet - callers (cleansing.py)
    treat an empty list as "fuzzy matching unavailable" and fall back to
    exact-alias matching only, so nothing breaks before the seed exists."""
    path = SEEDS_DIR / "musicbrainz_genres.txt"
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_genre_buckets() -> list[str]:
    """Curated <200-entry broad genre taxonomy (seeds/genre_buckets.txt) -
    see app/services/genre_buckets.py for the mapping logic that collapses
    MusicBrainz's ~2,200 fine-grained genres onto these."""
    path = SEEDS_DIR / "genre_buckets.txt"
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


COUNTRIES = load_countries()
MUSICBRAINZ_GENRES = load_musicbrainz_genres()
GENRE_BUCKETS = load_genre_buckets()
