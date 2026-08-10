"""Regenerate seeds/countries.csv from ISO 3166-1 data.

Why this exists rather than hand-editing the CSV: the `lastfm_name` column
must be the exact ISO 3166-1 name, because that's what Last.fm's
geo.getTopArtists matches on - "South Korea" silently returns nothing,
"Korea, Republic of" works. Typing 76 of those by hand would reintroduce
that bug several times over, so the names are derived from pycountry
instead of typed:

    lastfm_name  = pycountry's ISO 3166-1 name    (what the API wants)
    country_name = common name where one exists    (what humans read)

For kr that yields "Korea, Republic of" / "South Korea" - matching the
row that was originally worked out by hand after debugging the bug.

Run from the backend/ directory (needs `pip install -r requirements-dev.txt`):
    python -m scripts.generate_countries_seed

Note this OVERWRITES seeds/countries.csv. The generated file is committed;
this script only needs rerunning when the country list changes.
"""
import csv
import logging
from pathlib import Path

import pycountry

SEEDS_DIR = Path(__file__).resolve().parents[1] / "seeds"
OUTPUT_PATH = SEEDS_DIR / "countries.csv"

# Every country with a Spotify chart on kworb.net/spotify (the extractor's
# source), as of 2026-08. kworb only publishes charts for markets Spotify
# reports, so this is a hard ceiling on coverage - notably excluding China
# and most of Africa. Re-check https://kworb.net/spotify/ if Spotify adds
# markets; there's deliberately no live scrape here because a seed file
# shouldn't silently change under you on an unrelated run.
KWORB_COUNTRY_CODES = [
    # Europe
    "ad", "at", "be", "bg", "by", "ch", "cy", "cz", "de", "dk", "ee", "es",
    "fi", "fr", "gb", "gr", "hu", "ie", "is", "it", "lt", "lu", "lv", "mt",
    "nl", "no", "pl", "pt", "ro", "ru", "se", "sk", "tr", "ua",
    # Americas
    "ar", "bo", "br", "ca", "cl", "co", "cr", "do", "ec", "gt", "hn", "mx",
    "ni", "pa", "pe", "py", "sv", "us", "uy", "ve",
    # Asia-Pacific
    "au", "hk", "id", "in", "jp", "kr", "kz", "my", "nz", "ph", "pk", "sg",
    "th", "tw", "vn",
    # Middle East & Africa
    "ae", "eg", "il", "ma", "ng", "sa", "za",
]

# Where the current ISO name is NOT what Last.fm matches on. Fix mismatches
# here (one line, with a reason) rather than by hand-editing generated output,
# or the next regeneration silently reverts them.
LASTFM_NAME_OVERRIDES: dict[str, str] = {
    # ISO renamed Turkey -> Türkiye in 2022 and pycountry follows that, but
    # Last.fm's country index predates it: "Turkey" returns artists (this
    # project's own tr data was collected with it), "Türkiye" is unverified
    # and would fail silently by returning an empty artist list rather than
    # an error. Keeping the proven value until there's a reason to change.
    "tr": "Turkey",
    # The four below were each observed returning ZERO artists on the first
    # 76-country run, while sibling long-form names ("Korea, Republic of",
    # "Viet Nam") worked. Last.fm's country list is closer to everyday usage
    # than to formal ISO wording, so the formal variants find nothing:
    #   "Bolivia, Plurinational State of"    -> 0 artists
    #   "Czechia"                            -> 0 artists (ISO's 2016 rename)
    #   "Taiwan, Province of China"          -> 0 artists
    #   "Venezuela, Bolivarian Republic of"  -> 0 artists
    # Empty results here aren't just missing genres: the pipeline falls back
    # to parsing artist names out of kworb chart rows, which yields mangled
    # "Artist-Song" strings that then match nothing in Deezer either.
    "bo": "Bolivia",
    "cz": "Czech Republic",
    "tw": "Taiwan",
    "ve": "Venezuela",
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("generate_countries_seed")


def build_rows(codes: list[str]) -> list[dict]:
    """Pure function (code list -> CSV rows) so it's unit-testable without
    touching the filesystem - see tests/test_countries_seed.py."""
    rows = []
    for code in sorted(set(codes)):
        country = pycountry.countries.get(alpha_2=code.upper())
        if country is None:
            logger.warning("%s: no ISO 3166-1 match, skipping", code)
            continue
        iso_name = LASTFM_NAME_OVERRIDES.get(code, country.name)
        display_name = getattr(country, "common_name", None) or country.name
        rows.append(
            {
                "lastfm_name": iso_name,
                "kworb_code": code,
                "country_name": display_name,
            }
        )
    return rows


def main() -> None:
    rows = build_rows(KWORB_COUNTRY_CODES)
    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["lastfm_name", "kworb_code", "country_name"])
        writer.writeheader()
        writer.writerows(rows)

    differing = [r for r in rows if r["lastfm_name"] != r["country_name"]]
    logger.info("Wrote %d countries to %s", len(rows), OUTPUT_PATH)
    logger.info(
        "%d have an ISO name differing from their display name (e.g. %s)",
        len(differing),
        ", ".join(f"{r['kworb_code']}={r['lastfm_name']!r}" for r in differing[:3]),
    )


if __name__ == "__main__":
    main()
