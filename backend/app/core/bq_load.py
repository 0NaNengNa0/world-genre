"""Write primitives for the BigQuery warehouse.

Postgres gave idempotency for free: every insert was `ON CONFLICT DO UPDATE`
keyed on the natural key, so rerunning a day's load overwrote it. BigQuery has
no upsert and does not enforce primary keys, so rerunning would simply append
a second copy of every row. Idempotency has to be constructed, and there are
two shapes of it here:

- **Facts** (chart_entries, country_genre_scores, ...) are partitioned by
  snapshot_date and rewritten a whole day at a time: delete the partition,
  then append. The unit of atomicity is the day, which matches how the
  pipeline actually produces data - a run either has today's numbers or it
  doesn't.

- **Dimensions** (artists, genres) can't be rewritten, because their columns
  are filled in by *different* scripts across *different* runs -
  run_extract_artist_meta writes origin_country, run_extract_deezer writes
  deezer_fans. Truncating would discard hours of rate-limited enrichment. So
  those get MERGE, which inserts new keys and leaves existing enrichment
  alone.

Getting that distinction wrong is silent: a truncate-and-load on `artists`
would look like a successful run and quietly reset every origin to NULL,
taking the domestic-share metric down with it.
"""
import logging

from app.core.bq import _require_bigquery, dataset_id, get_client, run_statement

logger = logging.getLogger(__name__)


def table_ref(table: str) -> str:
    """Backtick-quoted `project.dataset.table`, safe to interpolate into SQL.

    Table names here are never caller-supplied - they're literals from this
    codebase - so interpolation is appropriate. A bind parameter cannot carry
    an identifier in any case.
    """
    return f"`{dataset_id()}.{table}`"


def delete_partition(table: str, snapshot_date) -> None:
    """Remove one day from a partitioned table.

    Filtering on the partitioning column is what keeps this cheap: BigQuery
    prunes to the single partition rather than scanning the table. A DELETE on
    a non-partitioning predicate would rewrite everything.
    """
    run_statement(
        f"DELETE FROM {table_ref(table)} WHERE snapshot_date = @snapshot_date",
        {"snapshot_date": snapshot_date},
    )


def append_rows(table: str, rows: list[dict]) -> int:
    """Append rows via a load job, returning how many were written.

    `load_table_from_json` rather than `INSERT` statements for two reasons:
    batch loads are free (INSERT DML is billed and rate-limited), and the rows
    land through the same path a file load would, so schema mismatches surface
    as a load error rather than as a partially-applied statement.

    Streaming inserts are deliberately avoided - they bill from the first byte
    and hold rows in a buffer that DELETE can't touch for up to 90 minutes,
    which would break the delete-then-append cycle above.
    """
    if not rows:
        return 0

    bigquery = _require_bigquery()
    client = get_client()
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        # The table already exists with the schema from sql/bigquery/schema.sql.
        # Autodetect would infer types from this batch instead - and would infer
        # them differently for a batch where every nullable measure happened to
        # be absent.
        autodetect=False,
    )
    job = client.load_table_from_json(
        rows, f"{dataset_id()}.{table}", job_config=job_config
    )
    job.result()
    return len(rows)


def replace_partition(table: str, snapshot_date, rows: list[dict]) -> int:
    """Rewrite one day of a fact table: delete the partition, then append.

    Not atomic. There is a window between the delete and the load where the
    day is empty, and a crash in between leaves it that way. That is
    acceptable here because the pipeline is a scheduled batch job that can be
    rerun, and the alternative - loading to a staging table and swapping -
    doubles the write cost to close a gap nobody is reading across.
    """
    delete_partition(table, snapshot_date)
    return append_rows(table, rows)


def merge_dimension(
    table: str, key: str, rows: list[dict], update_columns: list[str] | None = None
) -> int:
    """Insert new keys, optionally updating named columns on existing ones.

    `update_columns` is deliberately explicit rather than "all columns". The
    dimension tables are written by several scripts that each own different
    columns, so a blanket update would let the loader - which only knows
    artist names - blank out the origin_country and deezer_fans that the
    enrichment steps spent rate-limited API calls resolving.

    Passing None means insert-only: existing rows are left entirely alone.
    """
    if not rows:
        return 0

    staging = f"_staging_{table}"
    bigquery = _require_bigquery()
    client = get_client()

    # A real table rather than a temporary one, because BigQuery's temp tables
    # are scoped to a multi-statement script and this is two separate jobs.
    # WRITE_TRUNCATE makes it self-cleaning on each run.
    client.load_table_from_json(
        rows,
        f"{dataset_id()}.{staging}",
        job_config=bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            autodetect=True,
        ),
    ).result()

    columns = list(rows[0].keys())
    insert_cols = ", ".join(columns)
    insert_vals = ", ".join(f"S.{c}" for c in columns)

    update_clause = ""
    if update_columns:
        assignments = ", ".join(f"T.{c} = S.{c}" for c in update_columns)
        update_clause = f"WHEN MATCHED THEN UPDATE SET {assignments}"

    run_statement(
        f"""
        MERGE {table_ref(table)} T
        USING {table_ref(staging)} S
        ON T.{key} = S.{key}
        {update_clause}
        WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})
        """
    )
    return len(rows)
