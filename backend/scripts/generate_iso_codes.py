"""Regenerate frontend/src/lib/isoCodes.ts from seeds/countries.csv.

The pipeline keys countries by ISO 3166-1 alpha-2 (what kworb uses), but
world-map geometry files key their features by alpha-3 or by numeric code
depending on the source. The frontend needs to translate between them to
match a clicked map shape back to a country.

Deriving that table from pycountry keeps it exact. Hand-typing 76 country
codes is the same class of error that made South Korea silently return no
data for weeks - and a wrong code here fails the same quiet way, as a country
that simply never highlights on the map.

Run from the backend/ directory (needs `pip install -r requirements-dev.txt`):
    python -m scripts.generate_iso_codes
"""
import csv
import logging
from pathlib import Path

import pycountry

BACKEND_ROOT = Path(__file__).resolve().parents[1]
COUNTRIES_CSV = BACKEND_ROOT / "seeds" / "countries.csv"
OUTPUT_PATH = BACKEND_ROOT.parent / "frontend" / "src" / "lib" / "isoCodes.ts"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("generate_iso_codes")

_HEADER = """/**
 * ISO 3166-1 alpha-2 -> alpha-3 and numeric, for the countries this project
 * covers.
 *
 * GENERATED - do not hand-edit. Regenerate with:
 *   cd backend && python -m scripts.generate_iso_codes
 *
 * Why it exists: the pipeline keys countries by alpha-2 (kworb's codes),
 * but world map geometry files key features by alpha-3 or by numeric code
 * depending on the source. Deriving the mapping from pycountry keeps it
 * exact instead of hand-typing 76 entries, which is the same class of error
 * that made South Korea silently return no data.
 */
export type IsoCodes = { a3: string; numeric: string }

export const ISO_CODES: Record<string, IsoCodes> = {
"""

_FOOTER = """}

/** alpha-3 -> alpha-2, for looking up a clicked map feature. */
export const A3_TO_A2: Record<string, string> = Object.fromEntries(
  Object.entries(ISO_CODES).map(([a2, { a3 }]) => [a3, a2]),
)

/** numeric -> alpha-2, for map sources that key on numeric ids. */
export const NUMERIC_TO_A2: Record<string, string> = Object.fromEntries(
  Object.entries(ISO_CODES).map(([a2, { numeric }]) => [numeric, a2]),
)
"""


def build_entries(codes: list[str]) -> list[str]:
    """Pure function (alpha-2 codes -> TS object lines), so it's testable."""
    lines = []
    for code in sorted(set(codes)):
        country = pycountry.countries.get(alpha_2=code.upper())
        if country is None:
            logger.warning("%s: no ISO 3166-1 match, skipping", code)
            continue
        lines.append(f"  {code}: {{ a3: '{country.alpha_3}', numeric: '{country.numeric}' }},")
    return lines


def main() -> None:
    with COUNTRIES_CSV.open(newline="", encoding="utf-8") as f:
        codes = [row["kworb_code"] for row in csv.DictReader(f)]

    entries = build_entries(codes)
    OUTPUT_PATH.write_text(_HEADER + "\n".join(entries) + "\n" + _FOOTER, encoding="utf-8")
    logger.info("Wrote %d countries to %s", len(entries), OUTPUT_PATH)


if __name__ == "__main__":
    main()
