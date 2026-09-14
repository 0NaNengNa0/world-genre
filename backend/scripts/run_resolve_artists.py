"""Resolve artist origin from the MusicBrainz mirror - the batch stage.

    python -m scripts.run_resolve_artists
    python -m scripts.run_resolve_artists --dry-run

Runs sql/bigquery/merges/resolve_artists.sql, which fills `origin_country`,
`formed_year`, `mbid` and `resolved_by` on `artists` for every artist the
mirror can reach unambiguously. See that file for the three-tier rule and why
the third tier refuses rather than guesses.

WHERE THIS SITS. Immediately after `load` and before `enrich_artists`, so the
expensive API stage inherits a much smaller worklist. Both stages write the
same columns and both set `resolved_at`, so an artist resolved here is never
looked up over HTTP - which is the entire saving. Measured on 2026-09-14: the
API stage resolved ~50 artists a night against a backlog of 1,602 that was
growing by roughly as much as it cleared.

A run that resolves nothing is not an error. It means the mirror had nothing to
add since last time, which is the normal state between dump imports - the
import is weekly or monthly (scripts/run_import_mb_dump.py), not nightly.
"""
import argparse
import logging

from app.core.bq import dataset_id, run_query, run_statement
from app.core.config import SQL_DIR

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("run_resolve_artists")

MERGE_SQL = SQL_DIR / "bigquery" / "merges" / "resolve_artists.sql"


def merge_sql() -> str:
    """The statement with the dataset filled in.

    Plain str.replace rather than str.format, matching every other .sql file
    here - a format field collides with any brace appearing in a comment, and
    this file's comments are long.
    """
    return MERGE_SQL.read_text(encoding="utf-8").replace("{dataset}", dataset_id())


def _counts() -> dict:
    """Where the dimension stands: total, unresolved, and by resolution path."""
    rows = run_query(
        f"""
        SELECT
          COUNT(*) AS artists,
          COUNTIF(resolved_at IS NULL) AS unresolved,
          COUNTIF(origin_country IS NOT NULL) AS with_country,
          COUNTIF(resolved_by = 'mb_dump_mbid') AS by_mbid,
          COUNTIF(resolved_by = 'mb_dump_name') AS by_name
        FROM `{dataset_id()}.artists`
        """
    )
    return dict(rows[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="report the current state, change nothing"
    )
    args = parser.parse_args(argv)

    before = _counts()
    logger.info(
        "Before: %d artists, %d unresolved, %d with a country.",
        before["artists"],
        before["unresolved"],
        before["with_country"],
    )

    if args.dry_run:
        logger.info("Dry run - no MERGE issued.")
        return 0

    run_statement(merge_sql())

    after = _counts()
    logger.info(
        "Resolved %d this run (%d by mbid, %d by name in total). "
        "%d unresolved remain for the API stage.",
        before["unresolved"] - after["unresolved"],
        after["by_mbid"],
        after["by_name"],
        after["unresolved"],
    )
    if before["unresolved"] == after["unresolved"]:
        # Expected between dump imports; flagged rather than silent so that a
        # run of zero is a fact someone chose to see, not one they missed.
        logger.info(
            "Nothing new to resolve - the mirror has not changed since the last "
            "import (scripts/run_import_mb_dump.py)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
