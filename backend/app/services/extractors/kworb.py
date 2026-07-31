"""Scrape Spotify daily country chart tables from kworb.net (no API available).

URL pattern: https://kworb.net/spotify/country/{code}_daily.html
(e.g. us_daily.html, gb_daily.html, global_daily.html)

Pure extraction: the function returns rows, it does not write to disk. Callers
(scripts, tasks) decide what to do with the output.
"""
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://kworb.net/spotify/country/{code}_daily.html"
HEADERS = {"User-Agent": "student-data-eng-portfolio-project/1.0"}


def scrape_country_chart(code: str, timeout: int = 15) -> list[list[str]]:
    resp = requests.get(BASE_URL.format(code=code), headers=HEADERS, timeout=timeout)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.content, "lxml")
    table = soup.find("table")
    if table is None:
        return []

    rows = []
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if cells:
            rows.append(cells)
    return rows
