"""Validate every BigQuery statement without a network or credentials.

BigQuery has no local emulator, so the integration tests that ran against a
real Postgres binary have no equivalent. What can still be checked offline is
the thing the port could realistically break: dialect. sqlglot parses each
file as BigQuery and rejects anything Postgres-only.

That catches the whole class of error this migration introduces - a `double
colon` cast, a LATERAL join, a percent-style placeholder - at test time rather
than in a Cloud Run job at 6am.
"""
import re

import pytest

from app.core.config import SQL_DIR

sqlglot = pytest.importorskip("sqlglot")

SCHEMA = SQL_DIR / "bigquery" / "schema.sql"
QUERIES = sorted((SQL_DIR / "bigquery" / "queries").glob("*.sql"))

# Stand-ins for the values substituted at runtime, so the file parses as it
# will actually be sent.
SUBSTITUTIONS = {"{dataset}": "proj.world_genre", "{weight_column}": "score"}


def resolve(sql: str) -> str:
    for placeholder, value in SUBSTITUTIONS.items():
        sql = sql.replace(placeholder, value)
    return sql


@pytest.mark.parametrize("path", QUERIES, ids=lambda p: p.name)
class TestQueries:
    def test_parses_as_bigquery(self, path):
        sqlglot.parse_one(resolve(path.read_text(encoding="utf-8")), dialect="bigquery")

    def test_no_postgres_cast_syntax(self, path):
        # `double colon` casts parse permissively in sqlglot but BigQuery
        # rejects them outright. One of these was load-bearing in Postgres -
        # see the comment in hidden_gems.sql about integer division - so
        # dropping them was a semantic change, not a cosmetic one.
        assert "::" not in path.read_text(encoding="utf-8")

    def test_no_percent_style_placeholders(self, path):
        # Ported queries use @named parameters. A leftover percent-style one
        # would be sent to BigQuery as a literal and fail at runtime.
        assert "%(" not in path.read_text(encoding="utf-8")

    def test_parameters_are_named(self, path):
        # Every parameter must be a bare identifier: app/core/bq.py builds
        # ScalarQueryParameter objects from these names.
        for name in re.findall(r"@(\w*)", path.read_text(encoding="utf-8")):
            assert name, f"empty parameter name in {path.name}"

    def test_no_lateral_joins(self, path):
        assert "LATERAL" not in path.read_text(encoding="utf-8").upper()

    @pytest.mark.parametrize(
        "construct,replacement",
        [
            # Every one of these parses cleanly in sqlglot and is then
            # rejected by BigQuery at runtime, which is why parsing alone is
            # not sufficient validation. FILTER is not hypothetical: it
            # survived the port in three files and failed the first real
            # publish with "Expected ) but got keyword FILTER".
            ("FILTER (WHERE", "COUNTIF(cond) or SUM(CASE WHEN cond THEN x END)"),
            ("ILIKE", "LOWER(x) LIKE LOWER(y)"),
            ("SIMILAR TO", "REGEXP_CONTAINS"),
            ("DISTINCT ON", "QUALIFY ROW_NUMBER() OVER (...) = 1"),
            ("NOW()", "CURRENT_TIMESTAMP()"),
            ("SERIAL", "no auto-increment in BigQuery"),
            ("RETURNING", "not supported"),
            ("ON CONFLICT", "MERGE, or delete-partition-and-append"),
            ("::", "CAST(x AS type)"),
        ],
    )
    def test_no_postgres_only_constructs(self, path, construct, replacement):
        sql = path.read_text(encoding="utf-8")
        code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
        assert construct not in code.upper(), (
            f"{path.name} uses Postgres-only {construct!r}; use {replacement}"
        )


class TestSchema:
    def test_every_statement_parses(self):
        from scripts.run_init_bq import statements

        parsed = statements(SCHEMA.read_text(encoding="utf-8"), "proj.world_genre")
        assert len(parsed) == 9
        for statement in parsed:
            sqlglot.parse_one(statement, dialect="bigquery")

    def test_comments_are_stripped_before_splitting(self):
        """Order matters, and getting it wrong produces nonsense statements.

        The schema's own header contains "sql/schema.sql; this is a port".
        Splitting on semicolons first turns that into a statement boundary and
        hands a fragment of English prose to BigQuery - the same failure mode
        as a literal percent sign in a psycopg2 query.
        """
        from scripts.run_init_bq import statements

        schema = "-- a; b\nCREATE TABLE IF NOT EXISTS `d.t` (x INT64);"
        assert len(statements(schema, "d")) == 1

    def test_declares_keys_as_not_enforced(self):
        # BigQuery accepts PRIMARY KEY and FOREIGN KEY only with NOT ENFORCED;
        # omitting it is a syntax error. The declarations still help the
        # optimizer and document the grain, but guarantee nothing - which is
        # why run_load deduplicates before writing.
        #
        # Checked per line rather than by counting occurrences, because the
        # file's own comments discuss both phrases and a count conflates
        # prose with declarations.
        for line in SCHEMA.read_text(encoding="utf-8").splitlines():
            code = line.split("--", 1)[0]
            if "PRIMARY KEY" in code or "FOREIGN KEY" in code:
                assert "NOT ENFORCED" in code, line.strip()

    def test_fact_tables_are_partitioned_by_snapshot_date(self):
        # Partition pruning is what keeps reads cheap: BigQuery bills on bytes
        # scanned, so an unpartitioned fact table means every query pays for
        # every day ever loaded.
        sql = SCHEMA.read_text(encoding="utf-8")
        for table in ("chart_entries", "country_genre_scores"):
            section = sql.split(f"`{{dataset}}.{table}`", 1)[1]
            assert "PARTITION BY snapshot_date" in section.split("CREATE TABLE")[0]
