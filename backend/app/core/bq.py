"""BigQuery access, the counterpart to app/core/db.py.

Shaped deliberately unlike the Postgres module, because the two have opposite
cost models and copying the psycopg2 patterns across would be wrong:

- **No connection pool.** BigQuery is a stateless HTTPS API. There is no
  connection to keep open, so the pooling that made a 23x difference for
  Postgres has nothing to pool. The client object is reused only to avoid
  re-reading credentials.

- **No transactions.** There is no BEGIN/COMMIT to wrap a load in. Atomicity
  comes from operating on a whole partition at a time - see
  scripts/run_load.py.

- **Named parameters, not positional.** Postgres took `percent-s` and a tuple;
  BigQuery takes `@name` and typed parameter objects. Types are inferred from
  the Python value here rather than being spelled out at all 27 call sites.

Queries are billed on bytes scanned, so every read wants a snapshot_date
predicate to prune partitions. That's a property of the SQL in sql/bigquery/,
not of this module, but it's the reason the module exposes `dry_run_bytes` -
it makes the cost of a query checkable without running it.
"""
import datetime as dt
import os
from functools import lru_cache

# Default matches the dataset name used throughout DEPLOYMENT.md. The project
# is deliberately not defaulted: it must come from the environment or the
# client's own resolution, so nothing can silently write to the wrong one.
_DEFAULT_DATASET = "world_genre"


def _require_bigquery():
    """Import the client lazily, with an actionable message when absent.

    Same reasoning as app/core/storage.py: local development, the test suite
    and anything reading published JSON have no need for the Google client
    libraries, so they stay in requirements-cloud.txt rather than being a hard
    dependency of the package.
    """
    try:
        from google.cloud import bigquery
    except ImportError as e:
        raise RuntimeError(
            "google-cloud-bigquery isn't installed. Install the cloud extra: "
            "pip install -r requirements-cloud.txt"
        ) from e
    return bigquery


def dataset_id() -> str:
    """Fully-qualified `project.dataset`, from BQ_DATASET or BQ_PROJECT."""
    explicit = os.environ.get("BQ_DATASET")
    if explicit:
        return explicit

    project = os.environ.get("BQ_PROJECT") or os.environ.get(
        "GOOGLE_CLOUD_PROJECT"
    )
    if not project:
        raise RuntimeError(
            "Set BQ_DATASET to 'project.dataset', or BQ_PROJECT to the project "
            "id. Neither is defaulted, because guessing the project means "
            "risking a write to the wrong one."
        )
    return f"{project}.{_DEFAULT_DATASET}"


@lru_cache(maxsize=1)
def get_client():
    """Process-wide BigQuery client.

    Cached because constructing one resolves Application Default Credentials
    from disk or the metadata server; the client itself is thread-safe and
    holds no per-query state.
    """
    bigquery = _require_bigquery()
    project = os.environ.get("BQ_PROJECT") or dataset_id().split(".")[0]
    return bigquery.Client(project=project)


def reset_client() -> None:
    """Drop the cached client so a changed environment takes effect.

    Mirrors the pool-rebuild behaviour in db.py, which exists so tests can
    repoint the target without a stale handle silently serving the old one.
    """
    get_client.cache_clear()


def _scalar_parameter(name: str, value):
    bigquery = _require_bigquery()

    # bool before int: bool is a subclass of int in Python, so checking int
    # first would type every boolean as INT64.
    if isinstance(value, bool):
        type_ = "BOOL"
    elif isinstance(value, int):
        type_ = "INT64"
    elif isinstance(value, float):
        type_ = "FLOAT64"
    # datetime before date, for the same subclassing reason.
    elif isinstance(value, dt.datetime):
        type_ = "TIMESTAMP"
    elif isinstance(value, dt.date):
        type_ = "DATE"
    else:
        type_ = "STRING"
    return bigquery.ScalarQueryParameter(name, type_, value)


def _parameter(name: str, value):
    """Scalar or array parameter, chosen by the value's own shape.

    Array support exists so a query can take a whole list in one job - the
    genre panels query takes ARRAY<STRING> of genres rather than being called
    once per genre, which is the difference between 1,200 BigQuery jobs and
    76 for a publish run.
    """
    bigquery = _require_bigquery()
    if isinstance(value, (list, tuple)):
        if not value:
            # An empty array still needs an element type, and there is nothing
            # to infer it from. STRING is the only array this codebase passes.
            return bigquery.ArrayQueryParameter(name, "STRING", [])
        element = _scalar_parameter(name, value[0])
        return bigquery.ArrayQueryParameter(name, element.type_, list(value))
    return _scalar_parameter(name, value)


def _job_config(params: dict | None, dry_run: bool = False):
    bigquery = _require_bigquery()
    return bigquery.QueryJobConfig(
        query_parameters=[
            _parameter(name, value) for name, value in (params or {}).items()
        ],
        dry_run=dry_run,
        use_query_cache=not dry_run,
    )


def run_query(sql: str, params: dict | None = None) -> list[dict]:
    """Execute and return rows as plain dicts.

    Dicts rather than the client's Row objects so that callers - and the
    published JSON - never depend on a BigQuery type leaking outward. That
    matters more here than it did with psycopg2 tuples, because these results
    are serialised straight to JSON by the publish step.
    """
    job = get_client().query(sql, job_config=_job_config(params))
    return [dict(row.items()) for row in job.result()]


def run_statement(sql: str, params: dict | None = None) -> None:
    """Execute DDL or DML, discarding any result."""
    get_client().query(sql, job_config=_job_config(params)).result()


def dry_run_bytes(sql: str, params: dict | None = None) -> int:
    """Bytes this query would scan, without running or billing it.

    BigQuery charges per byte scanned, so this is the unit that matters for
    cost. Useful for confirming that a snapshot_date filter is actually
    pruning partitions rather than quietly scanning the whole table.
    """
    job = get_client().query(sql, job_config=_job_config(params, dry_run=True))
    return job.total_bytes_processed
