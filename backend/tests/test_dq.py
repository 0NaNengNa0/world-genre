"""The data-quality gate's own logic, tested without BigQuery.

The point of keeping thresholds in Python and only the measurement in SQL is
that the part which decides whether a run publishes can be tested exhaustively
offline. What cannot be tested here is whether each query measures the right
thing - test_bigquery_sql.py checks those parse, and only a real run proves
the numbers.

That split is deliberate, and the first real run proved why it is not
sufficient on its own: three of the five original checks were wrong in ways no
unit test could have caught. One divided by the wrong population, one was
structurally incapable of failing, and three reported vacuous passes on an
empty partition. The tests below encode the rules that came out of that -
especially that abstaining is not passing.
"""
import json

import pytest
from app.core import dq


def make_check(**overrides) -> dq.Check:
    defaults = {
        "name": "example",
        "description": "test fixture",
        "comparison": "min",
        "threshold": 0.8,
    }
    return dq.Check(**{**defaults, **overrides})


class TestCheckValidation:
    def test_rejects_unknown_comparison(self):
        with pytest.raises(ValueError, match="comparison"):
            make_check(comparison="approximately")

    def test_rejects_unreachable_warn_band_on_min(self):
        # Warning below the failure threshold can never fire: the check has
        # already failed by then. Caught at construction rather than showing
        # up as a warning nobody ever sees.
        with pytest.raises(ValueError, match="warn_threshold"):
            make_check(comparison="min", threshold=0.8, warn_threshold=0.5)

    def test_rejects_unreachable_warn_band_on_max(self):
        with pytest.raises(ValueError, match="warn_threshold"):
            make_check(comparison="max", threshold=0.2, warn_threshold=0.5)

    def test_accepts_warn_band_on_the_safe_side(self):
        make_check(comparison="min", threshold=0.8, warn_threshold=0.9)
        make_check(comparison="max", threshold=0.25, warn_threshold=0.2)


class TestEvaluateMin:
    @pytest.fixture
    def check(self):
        return make_check(comparison="min", threshold=0.8, warn_threshold=0.9)

    def test_above_warn_band_passes(self, check):
        assert dq.evaluate(check, 0.95).status == dq.PASS

    def test_inside_warn_band_warns(self, check):
        assert dq.evaluate(check, 0.85).status == dq.WARN

    def test_below_threshold_fails(self, check):
        assert dq.evaluate(check, 0.5).status == dq.FAIL

    def test_exactly_on_threshold_is_not_a_failure(self, check):
        # "At least" is inclusive. Spelled out because an off-by-one here is
        # the kind of thing that fires once a quarter and gets blamed on the
        # data rather than on the comparison.
        assert dq.evaluate(check, 0.8).status == dq.WARN
        assert dq.evaluate(make_check(threshold=0.8), 0.8).status == dq.PASS


class TestEvaluateMax:
    @pytest.fixture
    def check(self):
        return make_check(comparison="max", threshold=0.25, warn_threshold=0.2)

    def test_below_warn_band_passes(self, check):
        assert dq.evaluate(check, 0.1).status == dq.PASS

    def test_inside_warn_band_warns(self, check):
        assert dq.evaluate(check, 0.22).status == dq.WARN

    def test_above_threshold_fails(self, check):
        assert dq.evaluate(check, 0.4).status == dq.FAIL

    def test_zero_tolerance_check(self):
        # duplicate_chart_positions and countries_present are this shape:
        # anything above zero fails.
        check = make_check(comparison="max", threshold=0.0)
        assert dq.evaluate(check, 0.0).status == dq.PASS
        assert dq.evaluate(check, 1.0).status == dq.FAIL


class TestAbstaining:
    """An assertion over an empty set is vacuously true and worth nothing."""

    def test_zero_sample_size_skips_instead_of_passing(self):
        check = make_check(comparison="max", threshold=0.0)
        # Zero duplicates among zero rows is true and meaningless.
        result = dq.evaluate(check, 0.0, sample_size=0)
        assert result.status == dq.SKIP
        assert not result.blocked

    def test_zero_sample_size_skips_instead_of_failing(self):
        # Also true in the other direction: a coverage check with nothing to
        # cover must not report a failure that another check already owns.
        result = dq.evaluate(make_check(), 0.0, sample_size=0)
        assert result.status == dq.SKIP

    def test_a_populated_check_still_judges(self):
        assert dq.evaluate(make_check(), 0.95, sample_size=900).status == dq.PASS

    def test_no_sample_size_column_means_never_abstain(self):
        # chart_volume is the check that owns emptiness, so it declares no
        # sample_size and always renders a verdict.
        assert dq.evaluate(make_check(), 0.0).status == dq.FAIL


class TestFailClosed:
    def test_missing_value_is_an_error_not_a_pass(self):
        # A check that could not produce a number has not passed. The opposite
        # convention - treat unknown as fine - is how a broken check quietly
        # stops protecting anything while still appearing in the logs.
        result = dq.evaluate(make_check(), None)
        assert result.status == dq.ERROR
        assert result.blocked

    def test_error_is_blocking(self):
        assert dq.error(make_check(), "403 Access Denied").blocked

    def test_error_is_distinct_from_failure(self):
        # The distinction is the point: "the credentials expired" and "the
        # scrape came back empty" go to different people.
        assert dq.error(make_check(), "boom").status != dq.FAIL

    def test_non_blocking_check_downgrades_failure_to_warning(self):
        result = dq.evaluate(make_check(blocking=False), 0.1)
        assert result.status == dq.WARN
        assert not result.blocked

    def test_non_blocking_check_downgrades_errors_too(self):
        assert dq.error(make_check(blocking=False), "boom").status == dq.WARN


class TestRunCheck:
    def test_splits_value_from_context(self, monkeypatch):
        monkeypatch.setattr(dq, "check_sql", lambda check: "SELECT 1")
        monkeypatch.setattr(
            dq,
            "run_query",
            lambda sql, params: [{"value": 0.95, "observed_rows": 7000}],
        )
        result = dq.run_check(make_check(), "2026-09-14")
        assert result.status == dq.PASS
        assert result.value == pytest.approx(0.95)
        # Everything except `value` travels with the result, so a failure is
        # diagnosable from dq_runs without rerunning the query against data
        # that has since been overwritten.
        assert result.context == {"observed_rows": 7000}

    def test_sample_size_is_read_and_also_kept_in_context(self, monkeypatch):
        monkeypatch.setattr(dq, "check_sql", lambda check: "SELECT 1")
        monkeypatch.setattr(
            dq,
            "run_query",
            lambda sql, params: [{"value": None, "sample_size": 0, "changed_rows": 0}],
        )
        result = dq.run_check(make_check(), "2026-09-14")
        assert result.status == dq.SKIP
        assert result.context["sample_size"] == 0

    def test_query_error_becomes_an_error_result(self, monkeypatch):
        def explode(sql, params):
            raise RuntimeError("403 Access Denied")

        monkeypatch.setattr(dq, "check_sql", lambda check: "SELECT 1")
        monkeypatch.setattr(dq, "run_query", explode)

        result = dq.run_check(make_check(), "2026-09-14")
        assert result.status == dq.ERROR
        assert "403" in result.context["error"]

    def test_empty_result_set_is_an_error(self, monkeypatch):
        monkeypatch.setattr(dq, "check_sql", lambda check: "SELECT 1")
        monkeypatch.setattr(dq, "run_query", lambda sql, params: [])
        assert dq.run_check(make_check(), "2026-09-14").status == dq.ERROR


class TestTally:
    def test_counts_every_status_including_zeroes(self):
        results = [
            dq.evaluate(make_check(), 0.95),
            dq.evaluate(make_check(), 0.1),
            dq.evaluate(make_check(), 0.0, sample_size=0),
        ]
        counts = dq.tally(results)
        assert counts == {
            dq.PASS: 1,
            dq.WARN: 0,
            dq.FAIL: 1,
            dq.ERROR: 0,
            dq.SKIP: 1,
        }


class TestRegistry:
    def test_every_check_has_a_query_file(self):
        # The check's name IS the filename, so there is no mapping to keep in
        # sync - but a typo in the name would fail closed at pipeline time
        # instead of at test time without this.
        missing = [c.name for c in dq.CHECKS if not c.sql_path().exists()]
        assert not missing, f"no SQL file for: {missing}"

    def test_every_query_file_is_registered(self):
        registered = {c.name for c in dq.CHECKS}
        on_disk = {p.stem for p in dq.CHECK_DIR.glob("*.sql")}
        assert on_disk - registered == set(), "unregistered check file"

    def test_names_are_unique(self):
        names = [c.name for c in dq.CHECKS]
        assert len(names) == len(set(names))

    def test_every_check_takes_the_snapshot_date_parameter(self):
        # Every check is scoped to one partition, both so it measures the run
        # that just happened and so it does not scan the whole fact table.
        for check in dq.CHECKS:
            sql = check.sql_path().read_text(encoding="utf-8")
            assert "@snapshot_date" in sql, check.name

    def test_every_check_returns_a_value_column(self):
        for check in dq.CHECKS:
            sql = check.sql_path().read_text(encoding="utf-8")
            assert "AS value" in sql, check.name

    def test_only_chart_volume_declines_to_abstain(self):
        # Exactly one check must render a verdict on an empty partition,
        # otherwise an empty day produces five skips and reads as "nothing
        # wrong". Encoded as a test because it is a property of the SET of
        # checks, which no individual check can enforce.
        without_sample_size = {
            c.name
            for c in dq.CHECKS
            if "AS sample_size" not in c.sql_path().read_text(encoding="utf-8")
        }
        assert without_sample_size == {"chart_volume"}


class TestRecordShape:
    def test_rows_match_the_dq_runs_schema(self, monkeypatch):
        import datetime as dt

        written = {}

        def fake_append(table, rows):
            written["table"] = table
            written["rows"] = rows
            return len(rows)

        monkeypatch.setattr("app.core.bq_load.append_rows", fake_append)

        check = make_check(comparison="max", threshold=0.0)
        results = [dq.evaluate(check, 2.0, {"duplicate_keys": 2})]
        stamp = dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc)

        dq.record(results, dt.date(2026, 9, 14), run_ts=stamp)

        assert written["table"] == "dq_runs"
        row = written["rows"][0]
        assert row["check_name"] == "example"
        assert row["status"] == dq.FAIL
        assert row["snapshot_date"] == "2026-09-14"
        # Dates and timestamps go across as ISO strings: BigQuery's JSON load
        # path takes them that way, the same conversion run_load._iso makes.
        assert row["run_ts"].startswith("2026-09-14T")
        assert json.loads(row["context"]) == {"duplicate_keys": 2}

    def test_skips_are_recorded_too(self, monkeypatch):
        import datetime as dt

        written = {}
        monkeypatch.setattr(
            "app.core.bq_load.append_rows",
            lambda table, rows: written.setdefault("rows", rows) and len(rows),
        )
        results = [dq.evaluate(make_check(), 0.0, {"sample_size": 0}, sample_size=0)]
        dq.record(results, dt.date(2026, 9, 14))
        # Kept because "this check has been abstaining for a week" is exactly
        # the pattern a skip is supposed to make visible.
        assert written["rows"][0]["status"] == dq.SKIP


class TestValidateExitCodes:
    """The gate's exit code is an orchestrator's only input - get it right.

    run_validate returns 1 for "a check ran and the data failed it" and 2 for
    "a check could not run". The DAG maps the first onto AirflowFailException
    (terminal, skips the retry policy) and the second onto a retryable
    AirflowException, because retrying a deterministic assertion against an
    unchanged partition just burns two more attempts to learn the same thing.
    """

    def _exit_code(self, monkeypatch, results):
        from scripts import run_validate

        monkeypatch.setattr(dq, "run_checks", lambda day, checks=None: results)
        return run_validate.main(["--no-record"])

    def test_all_passing_exits_zero(self, monkeypatch):
        results = [dq.evaluate(make_check(), 0.99)]
        assert self._exit_code(monkeypatch, results) == 0

    def test_data_failure_exits_one(self, monkeypatch):
        results = [dq.evaluate(make_check(), 0.1)]
        assert self._exit_code(monkeypatch, results) == 1

    def test_execution_error_exits_two(self, monkeypatch):
        results = [dq.error(make_check(), "403 Access Denied")]
        assert self._exit_code(monkeypatch, results) == 2

    def test_data_failure_outranks_an_error(self, monkeypatch):
        # Both present: report the one a retry cannot fix, otherwise the
        # orchestrator retries its way around a loop it can never exit.
        results = [
            dq.error(make_check(), "network blip"),
            dq.evaluate(make_check(), 0.1),
        ]
        assert self._exit_code(monkeypatch, results) == 1

    def test_skips_alone_do_not_fail_the_run(self, monkeypatch):
        # A check that abstained has not failed. An empty enough partition is
        # chart_volume's problem to report, not everyone's.
        results = [dq.evaluate(make_check(), None, sample_size=0)]
        assert self._exit_code(monkeypatch, results) == 0

    def test_warnings_alone_do_not_fail_the_run(self, monkeypatch):
        check = make_check(comparison="min", threshold=0.8, warn_threshold=0.9)
        results = [dq.evaluate(check, 0.85)]
        assert self._exit_code(monkeypatch, results) == 0
