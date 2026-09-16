"""The BigQuery dry-run validator's offline half.

Everything here runs without credentials. The dry run itself cannot be tested
offline - that is the entire point of it, and pretending otherwise with a
mocked client would test the mock. What IS testable offline is the part that
decides WHAT gets sent: parameter resolution and statement collection. Both
have failed silently before in this repo, by skipping work rather than
erroring.
"""
import pytest

from scripts.run_validate_sql import (
    PARAM_SAMPLES,
    _params_for,
    _statements_to_check,
)


class TestParams:
    def test_known_parameters_resolve(self):
        params = _params_for("SELECT 1 WHERE code = @code AND n < @limit")
        assert set(params) == {"code", "limit"}

    def test_unknown_parameter_raises_rather_than_skipping(self):
        # The important one. A dry run given no value for a referenced
        # parameter fails with a BigQuery error about the parameter, which
        # reads like a broken query rather than a missing entry in this file.
        # Failing here names the actual problem.
        with pytest.raises(KeyError, match="no sample value declared"):
            _params_for("SELECT @nobody_declared_this")

    def test_no_parameters_is_fine(self):
        assert _params_for("SELECT 1") == {}

    def test_snapshot_date_is_a_date_not_a_string(self):
        # bq._parameter infers the BigQuery type from the Python type. A
        # string here would be typed STRING and fail to compare against a DATE
        # column - an error about this file, disguised as an error about the
        # check.
        import datetime as dt

        assert isinstance(PARAM_SAMPLES["snapshot_date"], dt.date)

    def test_every_sql_file_parameter_has_a_sample(self):
        # Guards the global map: adding a query that uses a new parameter name
        # fails here rather than at deploy time.
        for _, sql in _statements_to_check("p.d"):
            _params_for(sql)


@pytest.fixture(scope="module")
def labels():
    return [label for label, _ in _statements_to_check("p.d")]


class TestStatementCollection:
    def test_schema_statements_are_included(self, labels):
        # The 2026-09-14 incident was a schema statement nobody checked.
        assert sum(1 for label in labels if label.startswith("schema.sql")) == 15

    def test_queries_checks_and_merges_are_included(self, labels):
        for prefix in ("queries/", "checks/", "merges/"):
            assert any(label.startswith(prefix) for label in labels), prefix

    def test_both_weight_columns_are_validated(self, labels):
        # {weight_column} is a substituted identifier, not a bind parameter,
        # so each value is genuinely different SQL. Validating one leaves the
        # other branch of the genre donut unchecked.
        variants = [label for label in labels if "country_genre_shares" in label]
        assert len(variants) == 2
        assert any("[score]" in v for v in variants)
        assert any("[distinctiveness]" in v for v in variants)

    def test_read_only_mode_skips_every_writer(self):
        """Read-only credentials must skip BOTH kinds of writing statement.

        This was got wrong once: only the DDL was skipped, and the next run
        failed on the MERGE, which needs bigquery.tables.updateData for exactly
        the same reason CREATE TABLE needs tables.create. The axis is
        read-vs-write, not DDL-vs-query.
        """
        labels = [label for label, _ in _statements_to_check("p.d", include_writes=False)]
        assert not any(label.startswith("schema.sql") for label in labels)
        assert not any(label.startswith("merges/") for label in labels)

    def test_read_only_mode_still_validates_the_readers(self):
        """The half that catches real bugs must survive the skip.

        A regression that quietly dropped the queries too would defeat the gate
        while still reporting green - the worst possible failure for a check.
        """
        labels = [label for label, _ in _statements_to_check("p.d", include_writes=False)]
        for prefix in ("queries/", "checks/"):
            assert any(label.startswith(prefix) for label in labels), prefix

    def test_read_only_removes_exactly_the_writers(self):
        full = len(_statements_to_check("p.d", include_writes=True))
        without = len(_statements_to_check("p.d", include_writes=False))
        # 15 schema statements + 1 merge
        assert full - without == 16

    def test_dataset_placeholder_is_substituted(self):
        for label, sql in _statements_to_check("p.d"):
            assert "{dataset}" not in sql, label
