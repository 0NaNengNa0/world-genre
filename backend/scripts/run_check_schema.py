"""Compare sql/bigquery/schema.sql against what the warehouse actually has.

WHY THIS EXISTS. On 2026-09-14 two tables - mb_artists and mb_artist_names -
were found to have been created by a LOAD JOB rather than by schema.sql. A
BigQuery load job against a table that does not exist creates it, inferring
the schema from the data, so run_import_mb_dump.py brought both into being
before run_init_bq ever ran. `CREATE TABLE IF NOT EXISTS` is a no-op once the
table exists, so the CREATE statements had been silently doing nothing for as
long as the tables had existed, and neither carried the primary key its DDL
declared. Three pipeline stages then failed on one root cause thirty
statements upstream.

Nothing in the repo could have caught that, because nothing compared the
declared schema against the live one. The DDL and the warehouse are two
independent sources of truth that everything assumes are the same, and the
only thing that made them agree was that nobody had looked.

WHAT IT CHECKS, and what it deliberately does not:

  checked    table presence, column presence, column type, NOT NULL,
             partitioning column, clustering columns and their order
  NOT checked
             PRIMARY KEY / FOREIGN KEY. BigQuery never enforces them and does
             not expose them through INFORMATION_SCHEMA, so there is nothing
             to compare against. That is the one part of the 2026-09-14
             incident this cannot detect - worth stating plainly rather than
             implying a completeness it does not have.

An EXTRA column in the warehouse is reported but is not a failure on its own:
the ALTER TABLE ADD COLUMN pattern at the bottom of schema.sql means a column
legitimately arrives before the code that reads it, and a deploy that failed
halfway leaves exactly that state. A MISSING or MISTYPED column is a failure,
because code that writes it will fail or, worse, write somewhere it did not
mean to.

Run from the backend/ directory:
    python -m scripts.run_check_schema

Exit codes follow scripts/run_validate.py: 0 clean, 1 drift, 2 could not run.
"""
import logging
import re
import sys
from dataclasses import dataclass, field

from app.core.bq import dataset_id, run_query
from app.core.config import SQL_DIR

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("run_check_schema")

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_COULD_NOT_RUN = 2

SCHEMA_PATH = SQL_DIR / "bigquery" / "schema.sql"

# Tables bq_load.merge_dimension creates and drops inside a single run
# (`_staging_{table}`). They are an implementation detail of the merge, exist
# only for the seconds between a load and its MERGE, and are deliberately not
# in schema.sql - so reporting them is not a finding, it is noise that happens
# to depend on what time the check ran.
#
# This is not hypothetical: the nightly pipeline holds them for ~30 minutes,
# and a deploy landing in that window would list seven undeclared tables every
# time. A warning list that is sometimes wrong for reasons unrelated to the
# schema is a warning list people learn to skip.
STAGING_PREFIX = "_staging_"

# PARTITION BY / CLUSTER BY are read with a regex rather than from the parse
# tree, deliberately. They are single trailing clauses with a fixed shape, and
# sqlglot represents table properties differently across releases - so the
# regex is both simpler and less likely to break on an upgrade than walking
# the AST for them. Column definitions get the opposite treatment below: types
# genuinely need a parser, and a regex over them would be the fragile choice.
_PARTITION_RE = re.compile(r"PARTITION\s+BY\s+([A-Za-z_][\w]*)", re.IGNORECASE)
_CLUSTER_RE = re.compile(r"CLUSTER\s+BY\s+([\w\s,]+?)\s*$", re.IGNORECASE)
_ALTER_RE = re.compile(
    r"ALTER\s+TABLE\s+`?[\w.\-]*?\.?(\w+)`?\s+ADD\s+COLUMN\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+([\w<>, ]+?)\s*$",
    re.IGNORECASE,
)


@dataclass
class Column:
    name: str
    type: str
    not_null: bool = False


@dataclass
class Table:
    name: str
    columns: dict[str, Column] = field(default_factory=dict)
    partition_by: str | None = None
    cluster_by: list[str] = field(default_factory=list)


def _require_sqlglot():
    try:
        import sqlglot
    except ImportError as e:
        raise RuntimeError(
            "sqlglot isn't installed. It parses the declared DDL: "
            "pip install -r requirements-dev.txt"
        ) from e
    return sqlglot


def _normalise_type(raw: str) -> str:
    """One spelling for a type, so a cosmetic difference is not reported as drift.

    INFORMATION_SCHEMA and the DDL agree on the BigQuery standard names
    (STRING, INT64, ARRAY<STRING>), but differ in spacing inside parameterised
    types, and sqlglot may render a type it round-tripped with its own
    whitespace. Collapsing whitespace and upper-casing makes the comparison
    about the type rather than about formatting.
    """
    return re.sub(r"\s+", "", raw).upper()


def declared_tables() -> dict[str, Table]:
    """Parse schema.sql into the structure it claims the warehouse has."""
    sqlglot = _require_sqlglot()
    from sqlglot import exp

    from scripts.run_init_bq import statements

    tables: dict[str, Table] = {}

    # A placeholder dataset: the name is irrelevant here because only the
    # table's own identifier is kept, but {dataset} must be substituted or the
    # statements do not parse.
    for statement in statements(SCHEMA_PATH.read_text(encoding="utf-8"), "p.d"):
        alter = _ALTER_RE.search(statement.strip())
        if alter:
            table_name, column, type_ = alter.groups()
            if table_name in tables:
                tables[table_name].columns[column] = Column(
                    column, _normalise_type(type_)
                )
            continue

        parsed = sqlglot.parse_one(statement, dialect="bigquery")
        if not isinstance(parsed, exp.Create):
            continue

        table_expr = parsed.find(exp.Table)
        if table_expr is None:
            continue
        table = Table(name=table_expr.name)

        for column_def in parsed.find_all(exp.ColumnDef):
            kind = column_def.args.get("kind")
            not_null = any(
                isinstance(c.kind, exp.NotNullColumnConstraint)
                for c in column_def.constraints
            )
            table.columns[column_def.name] = Column(
                name=column_def.name,
                type=_normalise_type(kind.sql(dialect="bigquery") if kind else ""),
                not_null=not_null,
            )

        partition = _PARTITION_RE.search(statement)
        table.partition_by = partition.group(1) if partition else None

        cluster = _CLUSTER_RE.search(statement.strip())
        if cluster:
            table.cluster_by = [c.strip() for c in cluster.group(1).split(",") if c.strip()]

        tables[table.name] = table

    return tables


def live_tables(dataset: str) -> dict[str, Table]:
    """The warehouse's actual structure, from INFORMATION_SCHEMA.

    One query rather than four: INFORMATION_SCHEMA.COLUMNS carries the type,
    the nullability, the partitioning flag and the clustering position all on
    the column row, so the whole comparison set arrives in a single scan.
    """
    rows = run_query(
        f"""
        SELECT
            table_name,
            column_name,
            data_type,
            is_nullable,
            is_partitioning_column,
            clustering_ordinal_position
        FROM `{dataset}.INFORMATION_SCHEMA.COLUMNS`
        ORDER BY table_name, ordinal_position
        """
    )

    tables: dict[str, Table] = {}
    clustering: dict[str, list[tuple[int, str]]] = {}

    for row in rows:
        name = row["table_name"]
        table = tables.setdefault(name, Table(name=name))
        table.columns[row["column_name"]] = Column(
            name=row["column_name"],
            type=_normalise_type(row["data_type"]),
            not_null=row["is_nullable"] == "NO",
        )
        if row["is_partitioning_column"] == "YES":
            table.partition_by = row["column_name"]
        if row["clustering_ordinal_position"] is not None:
            clustering.setdefault(name, []).append(
                (row["clustering_ordinal_position"], row["column_name"])
            )

    for name, positions in clustering.items():
        tables[name].cluster_by = [c for _, c in sorted(positions)]

    return tables


def compare(declared: dict[str, Table], live: dict[str, Table]) -> tuple[list[str], list[str]]:
    """(failures, warnings). Failures mean the warehouse cannot serve the code."""
    failures: list[str] = []
    warnings: list[str] = []

    for name, want in sorted(declared.items()):
        have = live.get(name)
        if have is None:
            failures.append(f"{name}: declared in schema.sql, absent from the warehouse")
            continue

        for column, want_col in sorted(want.columns.items()):
            have_col = have.columns.get(column)
            if have_col is None:
                failures.append(f"{name}.{column}: declared, missing from the warehouse")
                continue
            if have_col.type != want_col.type:
                failures.append(
                    f"{name}.{column}: declared {want_col.type}, warehouse has {have_col.type}"
                )
            if want_col.not_null and not have_col.not_null:
                failures.append(
                    f"{name}.{column}: declared NOT NULL, warehouse has it nullable"
                )

        for column in sorted(set(have.columns) - set(want.columns)):
            warnings.append(f"{name}.{column}: in the warehouse, not declared in schema.sql")

        if want.partition_by != have.partition_by:
            failures.append(
                f"{name}: declared PARTITION BY {want.partition_by}, "
                f"warehouse has {have.partition_by}"
            )
        if want.cluster_by != have.cluster_by:
            failures.append(
                f"{name}: declared CLUSTER BY {want.cluster_by or None}, "
                f"warehouse has {have.cluster_by or None}"
            )

    for name in sorted(set(live) - set(declared)):
        if name.startswith(STAGING_PREFIX):
            continue
        warnings.append(f"{name}: in the warehouse, not declared in schema.sql")

    return failures, warnings


def main(argv: list[str] | None = None) -> int:
    try:
        dataset = dataset_id()
        declared = declared_tables()
    except RuntimeError as e:
        logger.error("%s", e)
        return EXIT_COULD_NOT_RUN

    logger.info("Comparing schema.sql (%d tables) against %s", len(declared), dataset)

    try:
        live = live_tables(dataset)
    except Exception as e:
        logger.error("Could not read INFORMATION_SCHEMA: %s", e)
        return EXIT_COULD_NOT_RUN

    failures, warnings = compare(declared, live)

    for warning in warnings:
        logger.warning("  %s", warning)

    if failures:
        logger.error("")
        logger.error("%d schema drift(s):", len(failures))
        for failure in failures:
            logger.error("  %s", failure)
        return EXIT_DRIFT

    logger.info(
        "No drift. %d declared tables match the warehouse%s.",
        len(declared),
        f" ({len(warnings)} undeclared extras)" if warnings else "",
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
