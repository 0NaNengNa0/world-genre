"""`match_name` must cover the whole dimension, not just today's chart.

This is a regression test for a measured failure, not a hypothetical. The
first real run of the MusicBrainz join resolved 295 artists and left 1,307
unresolved - and 742 of those had no match_name at all, because run_load only
ever computed the key for names on that day's charts. The join never looked at
more than half its backlog, and nothing said so: the column was populated, just
not everywhere.
"""
import types

import pytest
from scripts import run_load


@pytest.fixture
def warehouse(monkeypatch):
    """Stubs the BigQuery read so the artist list is whatever a test says."""
    def install(names):
        fake_bq = types.ModuleType("app.core.bq")
        fake_bq.dataset_id = lambda: "proj.ds"
        fake_bq.run_query = lambda sql: [{"artist_name": n} for n in names]
        monkeypatch.setitem(__import__("sys").modules, "app.core.bq", fake_bq)

    return install


class TestBackfillMatchNames:
    def test_covers_artists_absent_from_todays_chart(self, warehouse):
        # The whole point: BLACKPINK is in the dimension from an earlier run
        # and not on today's chart, so artist_rows() would never see it.
        warehouse(["BTS", "BLACKPINK"])
        rows = run_load.backfill_match_names()
        assert {r["artist_name"] for r in rows} == {"BTS", "BLACKPINK"}

    def test_key_is_match_key_not_the_raw_name(self, warehouse):
        # Both sides of the join must be normalised by identical Python -
        # that is the entire reason match_key is a named function.
        from app.services.cleansing import match_key

        warehouse(["BTS"])
        assert run_load.backfill_match_names()[0]["match_name"] == match_key("BTS")

    def test_skips_rows_with_no_name(self, warehouse):
        # artist_name is the merge key; a null one would produce a row that
        # matches nothing and inserts a nameless artist.
        warehouse(["BTS", None, ""])
        assert len(run_load.backfill_match_names()) == 1

    def test_every_row_carries_both_columns(self, warehouse):
        # merge_dimension derives its INSERT column list from the first row,
        # so a row missing match_name would silently narrow the statement.
        warehouse(["BTS", "aespa"])
        for row in run_load.backfill_match_names():
            assert set(row) == {"artist_name", "match_name"}
