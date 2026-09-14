"""Data-quality checks - the gate that decides whether a run may publish.

The pipeline already has two ways to fail loudly: an extractor raises, or a
load job rejects a row against the schema. Neither catches the failure that
actually matters here, which is a run that *succeeds* while carrying wrong
data - kworb serving a cached page, a country quietly dropping out of the
scrape, a duplicate key that BigQuery will not reject because it does not
enforce the primary keys schema.sql declares.

Where the gate sits is the whole design. Checks run against the warehouse
*after* the load and *before* run_publish, which means bad data lands in
BigQuery but never reaches the serving mart, and the API keeps serving the
last good JSON while the failure is investigated. That ordering has a name -
**write-audit-publish** - and the serving-mart architecture gives it away for
free: because the API never queries BigQuery, the warehouse is a staging area
that can hold a bad day without anyone seeing it.

Three deliberate choices:

- **A check is SQL returning one number, plus a threshold in Python.** The
  predicate is not in the SQL. That keeps the pass/fail logic unit-testable
  with no BigQuery (the same reason run_load.country_rows is pure), and it
  means each check maps one-to-one onto a dbt singular test if the
  transformation layer ever moves there - the .sql file survives, the
  threshold becomes test config.

- **Ratios, not absolute counts.** "At least 6,000 chart rows" has to be
  retuned every time a country is added to seeds/countries.csv, and nobody
  remembers to. "At least 95 percent of the trailing median" tunes itself.

- **Five statuses, not three.** pass / warn / fail is not enough vocabulary,
  and the missing two were both discovered the hard way on the first real
  run. ERROR means the check could not execute (a 403, a network failure) -
  it blocks, because fail-closed, but it is not a data problem and must not
  be filed as one. SKIP means the check had nothing to measure: an assertion
  over an empty set is vacuously true, and reporting that as a pass
  manufactures evidence of health that nobody verified. Exactly one check
  (chart_volume) owns emptiness; the rest abstain and let it speak.

A check declares it can abstain by returning a `sample_size` column. Zero
means skip. Checks with no such column - chart_volume - always render a
verdict.
"""
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

from app.core.bq import dataset_id, run_query
from app.core.config import SQL_DIR

logger = logging.getLogger(__name__)

CHECK_DIR = SQL_DIR / "bigquery" / "checks"

PASS = "pass"
WARN = "warn"
FAIL = "fail"
# The check could not run at all. Blocking, but a different kind of problem
# from bad data - it pages whoever owns credentials, not whoever owns the
# pipeline.
ERROR = "error"
# The check had nothing to measure. Neither a pass nor a failure.
SKIP = "skip"

BLOCKING_STATUSES = (FAIL, ERROR)

# "min": the value must stay at or above the threshold (coverage, volume).
# "max": the value must stay at or below it (error rates, missing countries).
COMPARISONS = ("min", "max")


@dataclass(frozen=True)
class Check:
    """One assertion: a query in sql/bigquery/checks, and the bound it must hold.

    `name` is also the .sql filename, so there is no separate registry mapping
    checks to files that can drift out of sync.
    """

    name: str
    description: str
    comparison: str
    threshold: float
    # Breaching this but not `threshold` records a warning and lets the run
    # continue - the early signal that a metric is drifting toward the gate
    # rather than the alarm that it has crossed it.
    warn_threshold: float | None = None
    blocking: bool = True

    def __post_init__(self) -> None:
        if self.comparison not in COMPARISONS:
            raise ValueError(
                f"{self.name}: comparison must be one of {COMPARISONS}, "
                f"got {self.comparison!r}"
            )
        if self.warn_threshold is None:
            return
        # A warn band on the wrong side of the fail threshold is unreachable:
        # the check fails before it can ever warn. Caught at import time
        # rather than discovered as a warning that never appears.
        if self.comparison == "min" and self.warn_threshold < self.threshold:
            raise ValueError(
                f"{self.name}: warn_threshold must be >= threshold for a min check"
            )
        if self.comparison == "max" and self.warn_threshold > self.threshold:
            raise ValueError(
                f"{self.name}: warn_threshold must be <= threshold for a max check"
            )

    def sql_path(self):
        return CHECK_DIR / f"{self.name}.sql"


@dataclass(frozen=True)
class Result:
    check: Check
    value: float | None
    status: str
    # The supporting numbers the query returned alongside `value` - the raw
    # counts behind a ratio, and for countries_present the codes themselves.
    # Recorded so a failure is diagnosable from dq_runs alone, without
    # rerunning the query against data that has since been overwritten.
    context: dict

    @property
    def blocked(self) -> bool:
        return self.status in BLOCKING_STATUSES

    def summary(self) -> str:
        if self.status == SKIP:
            return f"SKIP {self.check.name}: nothing to measure"
        if self.status == ERROR:
            return f"ERROR {self.check.name}: could not run"
        shown = "none" if self.value is None else f"{self.value:.4g}"
        bound = "at least" if self.check.comparison == "min" else "at most"
        return (
            f"{self.status.upper():4} {self.check.name}: {shown} "
            f"({bound} {self.check.threshold:.4g})"
        )


def _apply_severity(check: Check, status: str) -> str:
    """Non-blocking checks report; they never stop the run."""
    if status in BLOCKING_STATUSES and not check.blocking:
        return WARN
    return status


def error(check: Check, message: str) -> Result:
    """The check could not execute. Blocking, but filed as its own status.

    Distinguished from FAIL because "the credentials expired" and "the scrape
    came back empty" look identical in a log line and go to different people.
    """
    return Result(check, None, _apply_severity(check, ERROR), {"error": message})


def evaluate(
    check: Check,
    value: float | None,
    context: dict | None = None,
    sample_size: int | None = None,
) -> Result:
    """Pure: threshold logic with no BigQuery anywhere near it."""
    context = dict(context or {})

    # Abstain before judging. A check whose population is empty has not
    # verified anything, and saying so is more useful than a green line.
    if sample_size is not None and sample_size == 0:
        return Result(check, value, SKIP, context)

    if value is None:
        # The query ran and produced no number - a malformed check, not a
        # data problem, so it reads as an error rather than a failure.
        return error(check, "check returned no value")

    if check.comparison == "min":
        breached = value < check.threshold
        warned = check.warn_threshold is not None and value < check.warn_threshold
    else:
        breached = value > check.threshold
        warned = check.warn_threshold is not None and value > check.warn_threshold

    if breached:
        return Result(check, value, _apply_severity(check, FAIL), context)
    if warned:
        return Result(check, value, WARN, context)
    return Result(check, value, PASS, context)


def check_sql(check: Check) -> str:
    """The check's query with the dataset filled in.

    Plain str.replace rather than str.format, matching sql/bigquery/queries -
    a format field collides with any brace appearing in a comment.
    """
    sql = check.sql_path().read_text(encoding="utf-8")
    return sql.replace("{dataset}", dataset_id())


def run_check(check: Check, snapshot_date: date) -> Result:
    """Execute one check against the warehouse.

    Every check takes exactly one parameter, @snapshot_date, and returns
    exactly one row containing a `value` column. An optional `sample_size`
    column declares the check may abstain. Every other column is context.
    """
    try:
        rows = run_query(check_sql(check), {"snapshot_date": snapshot_date})
    except Exception as exc:  # noqa: BLE001 - any failure here is a failed check
        logger.debug("%s: check query failed", check.name, exc_info=True)
        return error(check, str(exc))

    if not rows:
        # An aggregate query should always return a row, so this means the
        # check's SQL was written wrong - which is itself worth blocking on.
        return error(check, "check query returned no rows")

    context = dict(rows[0])
    raw = context.pop("value", None)
    # Left in context on purpose as well as read: how big the population was
    # is worth keeping next to the measurement.
    sample_size = context.get("sample_size")
    return evaluate(
        check,
        None if raw is None else float(raw),
        context,
        None if sample_size is None else int(sample_size),
    )


def run_checks(snapshot_date: date, checks: list[Check] | None = None) -> list[Result]:
    return [run_check(c, snapshot_date) for c in (checks if checks is not None else CHECKS)]


def tally(results: list[Result]) -> dict[str, int]:
    """Count results by status, every status present even at zero."""
    counts = {status: 0 for status in (PASS, WARN, FAIL, ERROR, SKIP)}
    for result in results:
        counts[result.status] += 1
    return counts


def record(
    results: list[Result], snapshot_date: date, run_ts: datetime | None = None
) -> int:
    """Append results to dq_runs.

    Appended, never partition-replaced - the one fact table here that is not.
    Every other table answers "what is true for this day"; this one answers
    "what happened on each attempt", and a rerun that passes must not erase
    the attempt that failed. That history is also what turns a threshold from
    a guess into a measurement: every number in CHECKS below came from four
    real partitions, not from judgement.
    """
    from app.core.bq_load import append_rows

    stamp = run_ts or datetime.now(timezone.utc)
    rows = [
        {
            "run_ts": stamp.isoformat(),
            "snapshot_date": snapshot_date.isoformat(),
            "check_name": r.check.name,
            "status": r.status,
            "value": r.value,
            "threshold": r.check.threshold,
            "blocking": r.check.blocking,
            # JSON-in-STRING rather than BigQuery's JSON type: nothing queries
            # inside it yet, and a string round-trips through the same load
            # path as every other column with no special casing.
            "context": json.dumps(r.context, default=str),
        }
        for r in results
    ]
    return append_rows("dq_runs", rows)


# The registry. Every threshold here was set from four consecutive real
# partitions (2026-09-10 to 2026-09-13), with the observed range recorded in
# each description - so the next person to widen one has to argue with a
# number rather than with a guess.
CHECKS: list[Check] = [
    Check(
        name="chart_volume",
        description=(
            "Chart rows loaded today, as a fraction of the trailing 7-day median. "
            "Catches a partial or empty scrape - the most likely silent failure, "
            "because replace_partition deletes the day before appending, so an "
            "empty extract leaves an empty day rather than the previous one. "
            "Observed 0.9997-1.001 over four days (7,302-7,315 rows), which is "
            "why the floor is 0.95 and not something looser: this source is "
            "stable enough that a 5 percent drop is already an event. Note it "
            "cannot see one country vanishing - that is countries_present's job."
        ),
        comparison="min",
        threshold=0.95,
        warn_threshold=0.98,
    ),
    Check(
        name="countries_present",
        description=(
            "Countries seen in the trailing week but missing from today, counted. "
            "Exists because chart_volume provably cannot catch this: one country "
            "out of 75 is about 1.3 percent of rows, well inside the noise of a "
            "row-count ratio, so a country could drop out permanently and every "
            "other check would stay green. Compared against the trailing window "
            "rather than seeds/countries.csv on purpose - Andorra is in the seed "
            "list and has never charted, so a seed-list comparison would fail "
            "every single day and be switched off within a week."
        ),
        comparison="max",
        threshold=0.0,
    ),
    Check(
        name="chart_churn",
        description=(
            "Fraction of (country, position) slots whose track or artist changed "
            "since the previous snapshot. Near zero means the source served a "
            "cached page: the run succeeds, the row count is perfect, and the "
            "data is yesterday's. Observed 0.820-0.873 across four day-pairs. "
            "The floor is 0.30 rather than something near the observed range "
            "because chart turnover legitimately slows over holidays and quiet "
            "weeks; 0.60 warns so a genuine slide toward staleness is visible "
            "long before it blocks."
        ),
        comparison="min",
        threshold=0.30,
        warn_threshold=0.60,
    ),
    Check(
        name="duplicate_chart_positions",
        description=(
            "Count of (country, position) keys appearing more than once today. "
            "schema.sql declares that primary key NOT ENFORCED because BigQuery "
            "offers nothing else, so uniqueness is provided by run_load._dedupe "
            "and this is the assertion that it worked. Must be exactly zero: a "
            "duplicate silently doubles every downstream stream count. Observed "
            "zero across 7,315 rows."
        ),
        comparison="max",
        threshold=0.0,
    ),
    Check(
        name="artist_genre_coverage",
        description=(
            "Fraction of artists we fetched tags for that came out of cleansing "
            "with a genre. The denominator is country_artist_listeners, NOT "
            "chart_entries: genre signal comes from the Last.fm per-country top "
            "list, and schema.sql is explicit that Last.fm and the Spotify chart "
            "are different populations. An earlier version divided by charting "
            "artists, read 0.157, and blocked a publish over an overlap it had "
            "mistaken for coverage. Observed 0.948 (886 of 935)."
        ),
        comparison="min",
        threshold=0.85,
        warn_threshold=0.92,
    ),
    Check(
        name="unclassified_tag_rate",
        description=(
            "Share of raw genre tags cleansing could not classify, weighted by "
            "tag volume. Reads cleanse_quality, because the unclassified tags "
            "are dropped before scoring and are by construction absent from "
            "every other table. A rise means the tag vocabulary has moved "
            "underneath the bucket list - a slow decay in every genre donut, "
            "invisible to every other check here. "
            "NON-BLOCKING, and this is the point of the flag rather than an "
            "excuse: the ~0.18 figure quoted from run_cleanse's report is an "
            "average of per-country rates, while this is volume-weighted, so "
            "they are not the same statistic and no observation of THIS number "
            "exists yet. It also cannot be backfilled - the report kept only "
            "'latest'. Let dq_runs accumulate a fortnight, then promote it."
        ),
        comparison="max",
        threshold=0.35,
        warn_threshold=0.25,
        blocking=False,
    ),
]
