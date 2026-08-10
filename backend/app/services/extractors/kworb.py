"""Scrape Spotify daily country chart tables from kworb.net (no API available).

URL pattern: https://kworb.net/spotify/country/{code}_daily.html
(e.g. us_daily.html, gb_daily.html, global_daily.html)

Pure extraction: the function returns rows, it does not write to disk. Callers
(scripts, tasks) decide what to do with the output.
"""
import re

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://kworb.net/spotify/country/{code}_daily.html"
HEADERS = {"User-Agent": "student-data-eng-portfolio-project/1.0"}

# Column layout of the daily chart table, 0-indexed. Named rather than used
# as bare literals because the pipeline previously read row[2] in three
# separate files and nothing said what the other ten columns were.
COL_POSITION = 0
COL_POSITION_CHANGE = 1
COL_ARTIST_AND_TITLE = 2
COL_DAYS_ON_CHART = 3
COL_PEAK_POSITION = 4
COL_PEAK_WEEKS = 5  # "(x82)" - weeks spent at the peak position
COL_DAILY_STREAMS = 6
COL_DAILY_CHANGE = 7
COL_WEEKLY_STREAMS = 8
COL_WEEKLY_CHANGE = 9
COL_TOTAL_STREAMS = 10

_NUMBER_RE = re.compile(r"-?\d+")


def parse_number(value: str | None) -> int | None:
    """kworb's numeric cells -> int, or None when genuinely absent.

    Handles every shape the table uses: comma grouping ("1,608,958"), signed
    changes ("+25,456", "-153,180"), the parenthesised weeks-at-peak field
    ("(x82)"), and the placeholders it uses for no-value ("", "-", "*").
    Returns None rather than 0 for those, because a missing measure and a
    measured zero are different facts and averaging them together would be
    wrong.
    """
    if value is None:
        return None
    cleaned = value.replace(",", "").strip()
    if not cleaned:
        return None
    match = _NUMBER_RE.search(cleaned)
    if match is None:
        return None
    # A leading "+" is noise; "-" is meaningful and the regex keeps it.
    return int(match.group())


def parse_chart_entry(row: list[str]) -> dict | None:
    """One chart row -> a structured entry, or None if it isn't a data row.

    The scraper returns raw cell lists, which is the right shape for a
    faithful raw layer. This is where kworb's column layout is interpreted -
    once, rather than each consumer re-deriving it.

    Artist and track share a single cell ("Ella Langley-Choosin' Texas"),
    split on the same rule the rest of the pipeline uses, so a chart artist
    matches the same string a Last.fm artist does.
    """
    # Import here rather than at module scope: cleansing imports config,
    # which reads seed files, and the extractors are meant to stay importable
    # without that side effect.
    from app.services.cleansing import normalize_artist_name, parse_artist_from_chart_row

    if len(row) <= COL_ARTIST_AND_TITLE:
        return None

    label = row[COL_ARTIST_AND_TITLE]
    artist = normalize_artist_name(parse_artist_from_chart_row(label))
    if not artist:
        return None

    # Everything after the first separator is the track title. Falls back to
    # the whole label when there's no separator at all, which is rare but
    # happens for single-word entries.
    track = None
    for separator in (" - ", "-"):
        if separator in label:
            track = label.split(separator, 1)[1].strip()
            break

    def cell(index: int) -> str | None:
        return row[index] if len(row) > index else None

    return {
        "position": parse_number(cell(COL_POSITION)),
        "artist": artist,
        "track": track or None,
        "days_on_chart": parse_number(cell(COL_DAYS_ON_CHART)),
        "peak_position": parse_number(cell(COL_PEAK_POSITION)),
        "daily_streams": parse_number(cell(COL_DAILY_STREAMS)),
        "weekly_streams": parse_number(cell(COL_WEEKLY_STREAMS)),
        "total_streams": parse_number(cell(COL_TOTAL_STREAMS)),
    }


def parse_chart_rows(rows: list[list[str]]) -> list[dict]:
    """All parseable entries from a scraped table, header/junk rows dropped."""
    entries = [parse_chart_entry(row) for row in rows]
    return [e for e in entries if e and e["position"] is not None]


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
