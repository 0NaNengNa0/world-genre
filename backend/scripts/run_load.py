"""Load cleansed data into Postgres - the "load" stage of extract -> cleanse
-> load. Reads data/processed/{code}.json (written by run_cleanse.py) and
upserts into the warehouse tables defined in sql/schema.sql.

Idempotent: every insert is ON CONFLICT DO UPDATE keyed on
(country_code, ..., snapshot_date), so rerunning the same day's load never
duplicates rows - it just overwrites that day's numbers, which is exactly
what you want if a source got re-extracted and cleansed again the same day.

Run from the backend/ directory, after run_extract_kworb.py has been used
to populate run_init_db.py once and run_cleanse.py has run at least once:
    python -m scripts.run_init_db     # one-time
    python -m scripts.run_cleanse
    python -m scripts.run_load
"""
import json
import logging
from datetime import date, datetime, timezone

from app.core.config import COUNTRIES, DATA_DIR
from app.core.db import get_connection

PROCESSED_DIR = DATA_DIR / "processed"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("run_load")


def _load_processed(code: str) -> dict | None:
    path = PROCESSED_DIR / f"{code}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def load_country(cur, code: str, name: str, record: dict, snapshot_date: date) -> None:
    """Upserts one country's cleansed record into all four tables. Split
    out from main() so it's directly unit-testable against a real (or
    CI-provisioned) Postgres without needing files on disk - see
    tests/test_run_load.py."""
    cur.execute(
        """
        INSERT INTO countries (code, name) VALUES (%s, %s)
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
        """,
        (code, name),
    )

    cur.execute(
        """
        INSERT INTO country_snapshots (country_code, snapshot_date, artist_count)
        VALUES (%s, %s, %s)
        ON CONFLICT (country_code, snapshot_date)
        DO UPDATE SET artist_count = EXCLUDED.artist_count
        """,
        (code, snapshot_date, record.get("artist_count", 0)),
    )

    # run_cleanse writes one `genres` list carrying both scores per genre, so
    # this is a straight insert - no merging of separate popularity and
    # distinctiveness lists, which previously risked one overwriting the
    # other's columns with defaults.
    for row in record.get("genres", []):
        genre = row["genre"]
        cur.execute(
            """
            INSERT INTO country_genre_scores
                (country_code, genre, score, distinctiveness, sources, snapshot_date)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (country_code, genre, snapshot_date)
            DO UPDATE SET
                score = EXCLUDED.score,
                distinctiveness = EXCLUDED.distinctiveness,
                sources = EXCLUDED.sources
            """,
            (
                code,
                genre,
                row["score"],
                row.get("distinctiveness", 0.0),
                row["sources"],
                snapshot_date,
            ),
        )

    for rank, artist_name in enumerate(record.get("top_artists", []), start=1):
        cur.execute(
            """
            INSERT INTO country_top_artists (country_code, artist_name, rank, snapshot_date)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (country_code, snapshot_date, rank)
            DO UPDATE SET artist_name = EXCLUDED.artist_name
            """,
            (code, artist_name, rank, snapshot_date),
        )


def main() -> None:
    today = datetime.now(timezone.utc).date()
    loaded, skipped = 0, []

    with get_connection() as conn:
        with conn.cursor() as cur:
            for country in COUNTRIES:
                code, name = country["kworb_code"], country["country_name"]
                record = _load_processed(code)
                if record is None:
                    skipped.append(code)
                    logger.warning(
                        "%s: no processed data (run run_cleanse.py first), skipping", code
                    )
                    continue

                load_country(cur, code, name, record, today)
                loaded += 1
                logger.info("%s: loaded", code)

    logger.info(
        "Done. Loaded %d/%d countries into Postgres%s.",
        loaded,
        len(COUNTRIES),
        f" ({len(skipped)} skipped)" if skipped else "",
    )


if __name__ == "__main__":
    main()
