"""One-time download of MusicBrainz's canonical genre list.

    GET https://musicbrainz.org/ws/2/genre/all?fmt=txt

Per MusicBrainz's docs, the txt format returns every genre name in the
database in one response (alphabetical, newline-separated, no pagination
needed - limit/offset only apply to the json/xml formats). This is a
reference table, not something the recurring pipeline re-fetches - like
the Kaggle dataset, run it once and re-run only occasionally to pick up
new genres MusicBrainz has added.

Saves to seeds/musicbrainz_genres.txt, which app/core/config.py loads and
app/services/cleansing.py fuzzy-matches free-text tags against.

Run from the backend/ directory:
    python -m scripts.fetch_musicbrainz_genres
"""
import time

import requests

from app.core.config import SEEDS_DIR

URL = "https://musicbrainz.org/ws/2/genre/all"
HEADERS = {"User-Agent": "world-genre-portfolio-project/1.0 (hatsuneneng@gmail.com)"}
OUTPUT_PATH = SEEDS_DIR / "musicbrainz_genres.txt"
MAX_RETRIES = 3


def fetch_all_genres() -> list[str]:
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                URL,
                headers=HEADERS,
                params={"fmt": "txt"},
                timeout=30,
            )
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            wait = 2 ** (attempt + 1)
            print(f"  {e.__class__.__name__}, retrying in {wait}s "
                  f"(attempt {attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
            continue

        if resp.status_code == 503:
            wait = max(int(resp.headers.get("Retry-After", 0)), 2 ** (attempt + 1))
            print(f"  503 rate-limited, waiting {wait}s (attempt {attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
            continue

        resp.raise_for_status()
        return [line.strip() for line in resp.text.splitlines() if line.strip()]

    raise last_exc or requests.ConnectionError("failed to fetch genre list after retries")


def main() -> None:
    genres = fetch_all_genres()
    OUTPUT_PATH.write_text("\n".join(genres) + "\n", encoding="utf-8")
    print(f"Saved {len(genres)} canonical genres to {OUTPUT_PATH.resolve()}")


if __name__ == "__main__":
    main()
