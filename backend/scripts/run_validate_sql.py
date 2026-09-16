"""Validate every BigQuery statement against the live warehouse, without running it.

The third layer of SQL checking, and the one that catches what the other two
structurally cannot:

    ruff              - what is wrong with the Python
    sqlglot (tests)   - what is wrong with the dialect, offline
    this              - what is wrong with the ASSUMPTIONS: names, types,
                        scoping, and every other thing that only the real
                        warehouse knows

A dry run is a real BigQuery job submission with `dry_run=True`. The service
parses the SQL, resolves every table and column against the actual dataset,
type-checks the whole expression tree, and returns - having executed nothing
and billed nothing. It is the only check here that has ever seen the warehouse.

WHY sqlglot IS NOT ENOUGH. Both production failures in the week of 2026-09-14
parsed cleanly under sqlglot and then failed in a Cloud Run job:

  - a CTE output column shadowing its input inside HAVING. Valid syntax; the
    name resolves to the wrong thing, which no parser can know.
  - a CTE named `current`, a BigQuery reserved word. Valid to a generic
    parser; rejected by the engine.

Neither is a dialect error, so neither was catchable offline. Both are exactly
what name resolution against the real schema catches in milliseconds.

WHAT IT COSTS, AND WHAT IT REQUIRES. A dry run scans no bytes and is never
billed. It does NOT follow that it needs no permissions - that assumption cost
a red CI run on 2026-09-16.

A dry run performs the FULL authorization check, not just parsing. BigQuery
resolves every name and verifies access as if the query were about to execute;
the only thing it skips is execution itself. So validating a SELECT requires
`bigquery.tables.getData` on every table it touches - the same permission a
real query needs. `roles/bigquery.metadataViewer` grants tables.get and
tables.list, which is enough for INFORMATION_SCHEMA and NOT enough for this.

And the line that matters is READ vs WRITE, not DDL vs query. Any statement
that would modify something needs modify permission to validate:

    CREATE TABLE IF NOT EXISTS   ->  bigquery.tables.create
    MERGE INTO artists           ->  bigquery.tables.updateData

Both are write permissions. This was learned twice in one evening: the schema
statements were skipped first, and the next run still failed on
merges/resolve_artists.sql - the same insight, applied one category too
narrowly. Hence READ_ONLY_ENV rather than a DDL-shaped switch.

Granting write access to an identity whose entire purpose is to never write is
the wrong trade, so under READ_ONLY_ENV the writing statements are skipped.
Nothing is lost:

  - dialect errors in every .sql file, writers included, are already caught
    offline by sqlglot in tests/test_bigquery_sql.py,
  - schema.sql is EXECUTED nightly by run_init_bq as pipeline stage one and
    resolve_artists.sql by run_resolve_artists, both under a service account
    that legitimately has write access, so a broken statement fails within a
    day and before publish,
  - and scripts/run_check_schema.py compares the declared schema against
    INFORMATION_SCHEMA using metadata reads alone.

Locally, where the operator is usually project owner, everything is validated.

Run locally from the backend/ directory:
    python -m scripts.run_validate_sql

Exit codes match scripts/run_validate.py, for the same reason that file gives:
a caller must be able to tell "the SQL is wrong" from "the check could not
run", and collapsing them into one non-zero code destroys that distinction.
"""
import datetime as dt
import logging
import os
import re
import sys

from app.core.bq import dataset_id, dry_run_bytes
from app.core.config import SQL_DIR

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("run_validate_sql")

EXIT_OK = 0
EXIT_SQL_INVALID = 1
EXIT_COULD_NOT_RUN = 2

# Set to "1" wherever the credentials can read but not write - CI, above all.
# An environment variable rather than an argparse flag, on purpose: argparse
# exits 2 on a usage error, and 2 is already this script's "could not run"
# code. run_pipeline.py had to move its check-error code off 2 for exactly this
# reason. Re-introducing the same overloaded exit status to save four
# characters of ergonomics would be repeating a bug this repo has already paid
# for once.
READ_ONLY_ENV = "VALIDATE_SQL_READ_ONLY"

QUERY_DIR = SQL_DIR / "bigquery" / "queries"
CHECK_DIR = SQL_DIR / "bigquery" / "checks"
MERGE_DIR = SQL_DIR / "bigquery" / "merges"
SCHEMA_PATH = SQL_DIR / "bigquery" / "schema.sql"

# Sample values for every @parameter the .sql files use, keyed by parameter
# NAME rather than by file.
#
# Keyed globally because the names are shared and few - five across all 17
# files - so a per-file map would be seventeen copies of the same five facts,
# and adding a query that uses @code would mean editing this file for no
# reason. A parameter name that is NOT here fails loudly below rather than
# being silently skipped, which is what makes the global map safe: the only
# way to add an unknown parameter is to declare it.
#
# The VALUES are irrelevant to validation - a dry run type-checks the
# parameter and never evaluates it - but the TYPES are not. bq._parameter
# infers the BigQuery type from the Python value, so `1` here means INT64 and
# a date object means DATE. Getting one wrong produces a type error that is
# about this file, not about the query.
PARAM_SAMPLES: dict[str, object] = {
    "code": "us",
    "genre": "rock",
    "genres": ["rock", "pop"],
    "limit": 10,
    # A date object, not the string '2026-01-01': bq._parameter infers DATE
    # from the Python type, and a string would be typed STRING and fail to
    # compare against a DATE column.
    "snapshot_date": dt.date(2026, 1, 1),
}


def _weight_columns() -> list[str]:
    """Every value {weight_column} is ever substituted with, from the one
    place that decides it.

    Imported rather than hardcoded so there is a single source of truth. This
    matters more than it looks: {weight_column} is a substituted IDENTIFIER,
    not a bind parameter - it cannot be one, because no parameter can carry a
    column name - so each value produces genuinely different SQL. Validating
    only "score" would leave the entire distinctiveness branch of
    country_genre_shares.sql unchecked, which is the branch the genre donut
    uses in one of its two modes.
    """
    from scripts.run_publish import _WEIGHT_COLUMNS

    return sorted(set(_WEIGHT_COLUMNS.values()))


def _params_for(sql: str) -> dict:
    """The parameter dict this statement needs, or raise if one is unknown."""
    names = sorted(set(re.findall(r"@(\w+)", sql)))
    unknown = [n for n in names if n not in PARAM_SAMPLES]
    if unknown:
        raise KeyError(
            f"no sample value declared for parameter(s) {unknown}. Add them to "
            "PARAM_SAMPLES in this file - a dry run cannot type-check a "
            "parameter it was not given."
        )
    return {name: PARAM_SAMPLES[name] for name in names}


def _resolve(sql: str, dataset: str, weight_column: str | None = None) -> str:
    sql = sql.replace("{dataset}", dataset)
    if weight_column is not None:
        sql = sql.replace("{weight_column}", weight_column)
    return sql


def _statements_to_check(dataset: str, include_writes: bool = True) -> list[tuple[str, str]]:
    """(label, sql) for everything worth validating.

    The schema is included, not just the read queries. Its statements are the
    ones that define what every other statement resolves against, and the
    2026-09-14 incident - two tables brought into being by a load job, so
    their CREATE statements had been silent no-ops for weeks - is precisely a
    schema statement that nobody ever checked against production.
    """
    from scripts.run_init_bq import statements

    items: list[tuple[str, str]] = []

    if include_writes:
        for i, statement in enumerate(
            statements(SCHEMA_PATH.read_text(encoding="utf-8"), dataset), 1
        ):
            items.append((f"schema.sql[{i}]", statement))

    readable = (QUERY_DIR, CHECK_DIR) + ((MERGE_DIR,) if include_writes else ())
    for directory in readable:
        for path in sorted(directory.glob("*.sql")):
            raw = path.read_text(encoding="utf-8")
            label = f"{path.parent.name}/{path.name}"
            if "{weight_column}" in raw:
                for column in _weight_columns():
                    items.append(
                        (f"{label} [{column}]", _resolve(raw, dataset, column))
                    )
            else:
                items.append((label, _resolve(raw, dataset)))

    return items


def validate(dataset: str, include_writes: bool = True) -> list[tuple[str, str]]:
    """Dry-run everything. Returns [(label, error)] for what failed."""
    failures: list[tuple[str, str]] = []

    for label, sql in _statements_to_check(dataset, include_writes):
        try:
            params = _params_for(sql)
        except KeyError as e:
            failures.append((label, str(e)))
            continue

        try:
            # The returned byte count is discarded: this is a validity check,
            # not a cost check. dry_run_bytes is reused rather than reaching
            # into bq's internals, so there is one definition of "dry run".
            dry_run_bytes(sql, params)
        except Exception as e:
            # Deliberately broad. The google client raises a family of
            # exceptions here (BadRequest, NotFound, Forbidden) and the
            # message is the useful artefact in every case - this script's job
            # is to report what BigQuery said, not to classify it.
            failures.append((label, str(e).strip().splitlines()[0]))
            logger.error("FAIL %s", label)
        else:
            logger.info("ok   %s", label)

    return failures


def main(argv: list[str] | None = None) -> int:
    try:
        dataset = dataset_id()
    except RuntimeError as e:
        logger.error("%s", e)
        return EXIT_COULD_NOT_RUN

    include_writes = os.environ.get(READ_ONLY_ENV, "") != "1"
    logger.info(
        "Dry-running %s against %s",
        "every statement"
        if include_writes
        else "read-only statements (schema + merges skipped: no write access)",
        dataset,
    )

    try:
        failures = validate(dataset, include_writes)
    except Exception as e:
        # Could not reach BigQuery at all - missing credentials, no network,
        # no such dataset. Not the same as "the SQL is wrong", and a caller
        # that cannot tell them apart will either ignore real breakage or
        # block a deploy over an expired token.
        logger.error("Could not run validation: %s", e)
        return EXIT_COULD_NOT_RUN

    if failures:
        logger.error("")
        logger.error("%d statement(s) failed validation:", len(failures))
        for label, error in failures:
            logger.error("  %s", label)
            logger.error("      %s", error)
        return EXIT_SQL_INVALID

    logger.info("All statements validated against %s.", dataset)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
