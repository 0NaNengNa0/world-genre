"""Iterate all countries, scrape kworb charts, save top-100 rows per country.

Run from the backend/ directory:
    python -m scripts.run_extract_kworb
"""
import json
import time

import requests

from app.core.config import COUNTRIES, DATA_DIR
from app.services.extractors import kworb

OUTPUT_DIR = DATA_DIR / "raw" / "kworb"
TOP_N = 100


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for country in COUNTRIES:
        name, code = country["country_name"], country["kworb_code"]
        print(f"[kworb] {name} ...")

        try:
            rows = kworb.scrape_country_chart(code)
        except requests.HTTPError as e:
            print(f"  failed for {name}: {e}")
            continue

        (OUTPUT_DIR / f"{code}.json").write_text(
            json.dumps({"country": name, "rows": rows[:TOP_N]}, indent=2)
        )
        time.sleep(1.5)

    print(f"Done. Raw files in {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
