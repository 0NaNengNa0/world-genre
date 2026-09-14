"""Loading run_cleanse's quality report into the warehouse.

The row-shaping is pure (no file, no BigQuery client), for the same reason
run_load.country_rows is: there is no local BigQuery to test against, so the
only way to check the load logic offline is to keep it separable from the
load itself.
"""
import datetime as dt

from scripts.run_load import quality_rows

REPORT = {
    "us": {
        "artist_count": 100,
        "total_genre_tags": 480,
        "unclassified_genre_tags": 86,
        "unclassified_rate": 0.1792,
        "distinct_genres": 38,
    },
    "th": {
        "artist_count": 100,
        "total_genre_tags": 412,
        "unclassified_genre_tags": 91,
        "unclassified_rate": 0.2209,
        "distinct_genres": 31,
    },
}

DAY = dt.date(2026, 9, 14)


class TestQualityRows:
    def test_one_row_per_country(self):
        rows = quality_rows(REPORT, DAY)
        assert [r["country_code"] for r in rows] == ["th", "us"]

    def test_carries_the_partition_date_as_an_iso_string(self):
        # BigQuery's JSON load path takes DATE as a string, not a date object -
        # the same conversion every other fact row makes.
        assert quality_rows(REPORT, DAY)[0]["snapshot_date"] == "2026-09-14"

    def test_rate_is_recomputed_not_copied(self):
        # The report rounds to 4dp for human reading. Storing that rounded
        # value beside its own numerator and denominator would mean a later
        # query could get two different answers for the same question.
        row = next(r for r in quality_rows(REPORT, DAY) if r["country_code"] == "us")
        assert row["unclassified_rate"] == 86 / 480
        assert row["unclassified_rate"] != REPORT["us"]["unclassified_rate"]

    def test_no_tags_gives_a_null_rate_not_zero(self):
        # "No tags to classify" and "classified all of them" are different
        # facts, and 0.0 would make a country that produced nothing look
        # perfect - dragging the weighted average in the flattering direction.
        rows = quality_rows(
            {"ad": {"artist_count": 0, "total_genre_tags": 0,
                    "unclassified_genre_tags": 0, "distinct_genres": 0}},
            DAY,
        )
        assert rows[0]["unclassified_rate"] is None

    def test_missing_keys_do_not_raise(self):
        # The report is written by another script; a shape change should cost
        # this check its numbers, not break the whole load.
        rows = quality_rows({"us": {}}, DAY)
        assert rows[0]["total_genre_tags"] == 0
        assert rows[0]["unclassified_rate"] is None

    def test_empty_report_produces_no_rows(self):
        # Which makes the check abstain rather than assert on nothing.
        assert quality_rows({}, DAY) == []
