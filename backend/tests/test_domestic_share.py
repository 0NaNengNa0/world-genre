"""Domestic share: the number the product is built around.

It returned None for every country on every view until 2026-09-17, because
`_domestic_share` read column names neither query produces. Nothing caught it:
the SQL is valid and resolves, so the BigQuery dry run passes it, and each
layer is internally consistent - they only disagree across the boundary.

So the first test here does not exercise the function at all. It reads the real
.sql files and asserts they actually emit every column the shaper consumes.
That is the assertion that was missing, and it is deliberately the kind a
hand-written dict fixture cannot make: a fixture invented alongside the code
agrees with the code by construction.
"""
import re

import pytest

from app.core.config import SQL_DIR
from scripts.run_publish import _domestic_share

QUERY_DIR = SQL_DIR / "bigquery" / "queries"

# Every column _domestic_share reads off a row.
REQUIRED_COLUMNS = {
    "entry_count",
    "classified_entries",
    "total_streams",
    "classified_streams",
    "domestic_streams",
}


def _aliases(name: str) -> set[str]:
    """Output column names of a query, from its `AS alias` clauses."""
    sql = (QUERY_DIR / f"{name}.sql").read_text(encoding="utf-8")
    code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return {m.lower() for m in re.findall(r"\bAS\s+(\w+)", code, re.IGNORECASE)}


@pytest.mark.parametrize("name", ["domestic_share", "domestic_share_all"])
def test_query_emits_every_column_the_shaper_reads(name):
    """The contract test. Both variants feed the same function, so both must
    satisfy it - the all-countries one drifting from the per-country one is
    exactly how the map broke while looking fine."""
    missing = REQUIRED_COLUMNS - _aliases(name)
    assert not missing, f"{name}.sql does not emit {sorted(missing)}"


# Verbatim column list from the queries above, not an invented shape.
def row(**overrides) -> dict:
    base = {
        "total_streams": 1000,
        "classified_streams": 500,
        "domestic_streams": 200,
        "entry_count": 100,
        "classified_entries": 50,
    }
    base.update(overrides)
    return base


class TestShaping:
    def test_percentages_are_computed_from_the_raw_components(self):
        result = _domestic_share(row())
        assert result is not None
        # 200 of the 500 ATTRIBUTABLE streams are domestic.
        assert result["domestic_percentage"] == pytest.approx(40.0)
        # 500 of 1000 total streams could be attributed at all.
        assert result["coverage_percentage"] == pytest.approx(50.0)
        assert result["total_entries"] == 100
        assert result["classified_entries"] == 50

    def test_domestic_is_over_classified_not_over_total(self):
        """The denominator choice, pinned.

        Over total, this row would read 20 percent domestic - indistinguishable
        from a country that genuinely imports 80 percent of its listening. The
        difference between "not domestic" and "origin unknown" is the whole
        point of publishing coverage beside the figure.
        """
        result = _domestic_share(row(classified_streams=400, domestic_streams=200))
        assert result["domestic_percentage"] == pytest.approx(50.0)

    def test_coverage_is_stream_weighted_not_entry_weighted(self):
        """Half the entries can be a tenth of the listening. Streams are
        power-law distributed across a chart, so counting rows would overstate
        how much of a country's actual listening was attributed."""
        result = _domestic_share(
            row(total_streams=1000, classified_streams=100, entry_count=100, classified_entries=50)
        )
        assert result["coverage_percentage"] == pytest.approx(10.0)


class TestDegenerateRows:
    def test_nothing_classified_is_zero_not_a_crash(self):
        result = _domestic_share(row(classified_streams=0, domestic_streams=0))
        assert result["domestic_percentage"] == 0.0
        assert result["coverage_percentage"] == 0.0

    def test_no_streams_at_all_is_zero_not_a_crash(self):
        """Every stream column can COALESCE to 0 for a country that charted
        with null counts - real, and a ZeroDivisionError here would take down
        the whole publish rather than one country."""
        result = _domestic_share(
            row(total_streams=0, classified_streams=0, domestic_streams=0)
        )
        assert result["coverage_percentage"] == 0.0

    def test_no_entries_is_none(self):
        assert _domestic_share(row(entry_count=0)) is None

    def test_missing_row_is_none(self):
        assert _domestic_share(None) is None

    def test_the_old_wrong_shape_yields_none(self):
        """Regression guard, and a description of the bug.

        This is what the function used to expect. Feeding it the pre-computed
        shape must NOT silently work, or a future revert would look correct in
        tests while publishing None in production.
        """
        assert (
            _domestic_share(
                {
                    "domestic_percentage": 40.0,
                    "coverage_percentage": 50.0,
                    "classified_entries": 50,
                    "total_entries": 100,
                }
            )
            is None
        )
