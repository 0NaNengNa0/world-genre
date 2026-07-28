"""
Scrape Spotify daily country chart tables from kworb.net (no API available).

URL pattern confirmed: https://kworb.net/spotify/country/{code}_daily.html
(e.g. us_daily.html, gb_daily.html, global_daily.html)

This is the one source with no formal API, so no key needed - but be a good
citizen: check https://kworb.net/robots.txt before scraping at scale, keep
requests slow (there's a sleep() below), and set a real User-Agent.

Output: one raw JSON file per country under raw/kworb/{country}.json
"""
import json
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from countries import COUNTRIES

BASE_URL = "https://kworb.net/spotify/country/{code}_daily.html"
OUTPUT_DIR = Path("raw/kworb")
HEADERS = {"User-Agent": "student-data-eng-portfolio-project/1.0"}


def scrape_country_chart(code: str) -> list[dict]:
    url = BASE_URL.format(code=code)
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    table = soup.find("table")
    if table is None:
        return []

    rows = []
    for tr in table.find_all("tr")[1:]:  # skip header row
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if cells:
            rows.append(cells)
    return rows


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for country in COUNTRIES:
        name, code = country["lastfm_name"], country["kworb_code"]
        print(f"[kworb] {name} ...")

        try:
            rows = scrape_country_chart(code)
        except requests.HTTPError as e:
            print(f"  failed for {name}: {e}")
            continue

        out_path = OUTPUT_DIR / f"{code}.json"
        out_path.write_text(json.dumps({"country": name, "rows": rows}, indent=2))

        time.sleep(1.5)  # scraping - be slower than the API calls

    print(f"Done. Raw files in {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
