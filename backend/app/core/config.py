"""Shared configuration and seed loaders.

Keeps a single place to answer: where do settings and reference data come from?
Extractors, scripts, and API routes all import from here so a change to the
countries list or an env var only touches one file.
"""
import csv
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SEEDS_DIR = BACKEND_ROOT / "seeds"
DATA_DIR = BACKEND_ROOT / "data"


def load_countries() -> list[dict]:
    with (SEEDS_DIR / "countries.csv").open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


COUNTRIES = load_countries()
