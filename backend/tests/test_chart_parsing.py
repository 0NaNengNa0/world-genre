"""Tests for kworb chart-row parsing.

This is where 10 of kworb's 11 columns finally get interpreted - the pipeline
previously read only row[2] and discarded position, chart longevity and every
stream figure. The rows below are copied from real scraped output, quirks
intact: comma grouping, signed changes, the "(x82)" weeks-at-peak field, and
the unspaced "Artist-Song" cell.
"""
from app.services.extractors.kworb import parse_chart_entry, parse_chart_rows, parse_number

# Verbatim from data/raw/kworb/us.json
REAL_ROW = [
    "1", "=", "Ella Langley-Choosin' Texas", "296", "1", "(x82)",
    "1,608,958", "+25,456", "10,108,462", "+29,225", "381,256,238",
]


class TestParseNumber:
    def test_comma_grouped(self):
        assert parse_number("1,608,958") == 1608958

    def test_signed_changes(self):
        assert parse_number("+25,456") == 25456
        assert parse_number("-153,180") == -153180

    def test_parenthesised_weeks_field(self):
        assert parse_number("(x82)") == 82

    def test_absent_values_are_none_not_zero(self):
        # A missing measure and a measured zero are different facts; folding
        # them together would quietly bias any average over these columns.
        for blank in (None, "", "   ", "-", "*"):
            assert parse_number(blank) is None

    def test_plain_integer(self):
        assert parse_number("296") == 296


class TestParseChartEntry:
    def test_extracts_every_column(self):
        entry = parse_chart_entry(REAL_ROW)
        assert entry == {
            "position": 1,
            "artist": "Ella Langley",
            "track": "Choosin' Texas",
            "days_on_chart": 296,
            "peak_position": 1,
            "daily_streams": 1608958,
            "weekly_streams": 10108462,
            "total_streams": 381256238,
        }

    def test_splits_artist_from_track_on_the_unspaced_dash(self):
        entry = parse_chart_entry(["5", "=", "BTS-NORMAL", "10", "5", "(x1)", "1,000"])
        assert entry["artist"] == "BTS"
        assert entry["track"] == "NORMAL"

    def test_handles_a_spaced_separator(self):
        entry = parse_chart_entry(["1", "=", "Jay-Z - 99 Problems", "1", "1", "(x1)", "5"])
        # " - " is tried first, so a hyphenated artist name survives intact.
        assert entry["artist"] == "Jay-Z"
        assert entry["track"] == "99 Problems"

    def test_short_row_still_yields_what_it_can(self):
        # kworb truncates trailing columns on some entries; the row is still
        # a real chart position and shouldn't be thrown away.
        entry = parse_chart_entry(["7", "=", "Artist-Song"])
        assert entry["position"] == 7
        assert entry["artist"] == "Artist"
        assert entry["daily_streams"] is None

    def test_row_without_an_artist_cell_is_rejected(self):
        assert parse_chart_entry(["1", "="]) is None

    def test_unicode_track_titles_survive(self):
        entry = parse_chart_entry(["2", "=", "Mrs. GREEN APPLE-青と夏", "1", "1", "(x1)", "1"])
        assert entry["artist"] == "Mrs. GREEN APPLE"
        assert entry["track"] == "青と夏"


class TestParseChartRows:
    def test_drops_rows_without_a_position(self):
        rows = [REAL_ROW, ["", "", "Header-ish", "", ""], ["2", "=", "B-Song", "1", "1"]]
        parsed = parse_chart_rows(rows)
        assert [p["position"] for p in parsed] == [1, 2]

    def test_empty_input(self):
        assert parse_chart_rows([]) == []
