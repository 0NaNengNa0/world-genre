"""The schema-drift checker, tested against the real schema.sql.

The fixture is the actual file, not a miniature of it, because the thing most
likely to break here is an assumption about the real file's shape - an ALTER
that adds a column to a table declared 300 lines earlier, a FOREIGN KEY whose
REFERENCES clause names a second table inside a CREATE, a CLUSTER BY with two
columns. A hand-written two-column fixture confirms the parser handles the
case its author was already thinking about, which is the failure mode called
out in claude/backend-map.md: a test written from the same assumption as the
code can only agree with it.
"""
import pytest

from scripts.run_check_schema import (
    STAGING_PREFIX,
    Column,
    Table,
    compare,
    declared_tables,
)

pytest.importorskip("sqlglot")


@pytest.fixture(scope="module")
def declared():
    return declared_tables()


class TestDeclaredParsing:
    def test_parses_every_table(self, declared):
        # 13 CREATE TABLE statements; the other 2 of the schema's 15 are the
        # ALTERs, which attach to a table rather than creating one.
        assert len(declared) == 13

    def test_alter_added_columns_attach_to_their_table(self, declared):
        # The whole reason ALTER exists at the bottom of schema.sql: CREATE
        # TABLE IF NOT EXISTS is a no-op against an existing table, so a new
        # column HAS to arrive this way. A parser that only read CREATEs would
        # report both of these as drift on every single run.
        artists = declared["artists"]
        assert artists.columns["match_name"].type == "STRING"
        assert artists.columns["resolved_by"].type == "STRING"

    def test_constraints_are_not_mistaken_for_columns(self, declared):
        # PRIMARY KEY and FOREIGN KEY sit in the same parenthesised list as the
        # column definitions. country_snapshots declares both, and has exactly
        # three real columns.
        assert set(declared["country_snapshots"].columns) == {
            "country_code",
            "snapshot_date",
            "artist_count",
        }

    def test_foreign_key_target_is_not_mistaken_for_the_table(self, declared):
        # FOREIGN KEY ... REFERENCES `{dataset}.countries` puts a second table
        # identifier inside the CREATE. Taking the wrong one would name every
        # fact table "countries".
        assert "country_genre_scores" in declared
        assert declared["country_genre_scores"].partition_by == "snapshot_date"

    def test_partitioning_and_clustering(self, declared):
        chart = declared["chart_entries"]
        assert chart.partition_by == "snapshot_date"
        assert chart.cluster_by == ["country_code", "artist_name"]

    def test_dimensions_are_not_partitioned(self, declared):
        assert declared["artists"].partition_by is None
        assert declared["artists"].cluster_by == ["artist_name"]

    def test_not_null_is_captured(self, declared):
        chart = declared["chart_entries"]
        assert chart.columns["country_code"].not_null
        assert not chart.columns["track_name"].not_null

    def test_array_type_survives_normalisation(self, declared):
        assert declared["country_genre_scores"].columns["sources"].type == "ARRAY<STRING>"


def _table(**columns) -> Table:
    return Table(name="t", columns={k: Column(k, v) for k, v in columns.items()})


class TestCompare:
    def test_clean_when_identical(self):
        declared = {"t": _table(a="STRING")}
        assert compare(declared, {"t": _table(a="STRING")}) == ([], [])

    def test_missing_table_fails(self):
        failures, _ = compare({"t": _table(a="STRING")}, {})
        assert failures and "absent from the warehouse" in failures[0]

    def test_missing_column_fails(self):
        failures, _ = compare({"t": _table(a="STRING", b="INT64")}, {"t": _table(a="STRING")})
        assert any("t.b" in f and "missing" in f for f in failures)

    def test_type_mismatch_fails(self):
        # The quiet one. An all-NULL column infers as STRING on a load job, so
        # a column declared INT64 can exist, be populated, and be the wrong
        # type - which breaks a MERGE that worked yesterday.
        failures, _ = compare({"t": _table(a="INT64")}, {"t": _table(a="STRING")})
        assert any("declared INT64" in f and "STRING" in f for f in failures)

    def test_extra_column_warns_but_does_not_fail(self):
        # A deploy that added the column before the code that reads it is a
        # legitimate intermediate state, not a broken warehouse.
        failures, warnings = compare(
            {"t": _table(a="STRING")}, {"t": _table(a="STRING", b="INT64")}
        )
        assert failures == []
        assert any("t.b" in w for w in warnings)

    def test_nullability_drift_fails_only_in_the_dangerous_direction(self):
        strict = Table(name="t", columns={"a": Column("a", "STRING", not_null=True)})
        loose = Table(name="t", columns={"a": Column("a", "STRING", not_null=False)})
        # Declared NOT NULL, warehouse nullable: code may write a NULL the
        # schema says is impossible.
        assert compare({"t": strict}, {"t": loose})[0]
        # The reverse is the warehouse being stricter than the declaration,
        # which cannot surprise a reader.
        assert compare({"t": loose}, {"t": strict})[0] == []

    def test_staging_tables_are_not_reported(self):
        # merge_dimension creates `_staging_{table}` and drops it within the
        # run. During the nightly window the warehouse genuinely contains
        # seven tables schema.sql does not declare, and none of them is drift.
        live = {
            "t": _table(a="STRING"),
            f"{STAGING_PREFIX}t": _table(a="STRING"),
            f"{STAGING_PREFIX}artists": _table(artist_name="STRING"),
        }
        failures, warnings = compare({"t": _table(a="STRING")}, live)
        assert failures == []
        assert warnings == []

    def test_clustering_drift_fails(self):
        # This is the 2026-09-14 shape: a table created by a load job has no
        # clustering at all, because a load job infers columns and nothing
        # else. The CREATE that declared it had been a no-op for weeks.
        want = Table(name="t", columns={"a": Column("a", "STRING")}, cluster_by=["a"])
        have = Table(name="t", columns={"a": Column("a", "STRING")}, cluster_by=[])
        failures, _ = compare({"t": want}, {"t": have})
        assert any("CLUSTER BY" in f for f in failures)
