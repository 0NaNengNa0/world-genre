"""Create the BigQuery warehouse tables - the counterpart to run_init_db.py.

Idempotent: every statement is CREATE TABLE IF NOT EXISTS, so this is safe to
run against an existing dataset on every pipeline run.

    python -m scripts.run_init_bq

Requires BQ_DATASET (or BQ_PROJECT) - see app/core/bq.py.
"""
import logging

from app.core.bq import dataset_id, run_statement
from app.core.config import SQL_DIR

SCHEMA_PATH = SQL_DIR / "bigquery" / "schema.sql"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("run_init_bq")


def statements(schema_sql: str, dataset: str) -> list[str]:
    """Split the schema file into individually-executable statements.

    BigQuery's API takes one statement per job, unlike psycopg2 which happily
    executes a whole file in one call.

    Comments are stripped *before* splitting on semicolons, and that ordering
    is the whole point. The schema's own header contains the phrase
    "sql/schema.sql; this is a port", and splitting first turns that semicolon
    into a statement boundary - producing a fragment of English prose that
    BigQuery is then asked to execute. This is the same failure as the literal
    percent signs the .sql files avoid: characters that are inert in prose but
    load-bearing to a parser.

    Stripping to end-of-line on `--` is safe only while the schema contains no
    string literals (a literal could hold a `--`, which would then be
    truncated). It has none today, and adding one means revisiting this.

    Substitution is `str.replace`, not `str.format`, for the same reason as
    sql/queries: a format field collides with any brace elsewhere in the file.
    """
    resolved = schema_sql.replace("{dataset}", dataset)

    uncommented = "\n".join(
        line.split("--", 1)[0] for line in resolved.splitlines()
    )
    return [s.strip() for s in uncommented.split(";") if s.strip()]


def main() -> None:
    dataset = dataset_id()
    logger.info("Applying schema to %s", dataset)

    for statement in statements(SCHEMA_PATH.read_text(encoding="utf-8"), dataset):
        # First line of each statement is enough to identify it in the log,
        # and avoids dumping ~180 lines of DDL on every pipeline run.
        first_line = statement.splitlines()[0].strip()
        logger.info("  %s", first_line)
        run_statement(statement)

    logger.info("Schema applied.")


if __name__ == "__main__":
    main()
