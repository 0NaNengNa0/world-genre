"""Tests for kworb chart-row parsing.

This is where 10 of kworb's 11 columns finally get interpreted - the pipeline
previously read only row[2] and discarded position, chart longevity and every
stream figure. The rows below are copied from real scraped output, quirks
intact: comma grouping, signed changes, the "(x82)" weeks-at-peak field, and
the unspaced "Artist-Song" cell.
"""
from app.services.cleansing import parse_artist_from_chart_row
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

    def test_a_spaced_dash_is_part_of_the_title(self):
        """This test used to assert the opposite, and that is the lesson.

        It fed in "Jay-Z - 99 Problems" - a label with spaces around the
        separator - and asserted the parser split there. kworb does not emit
        that shape. Across a full chart page, all 100 labels were
        "Artist-Title" with a bare hyphen, and " - " appeared only inside
        titles as a suffix.

        So the test invented an input to confirm the belief the code was
        written from, and the two agreed with each other for months while
        production disagreed with both. A test written from the same
        assumption as the code can only ever confirm it.
        """
        entry = parse_chart_entry(
            ["26", "+5", "Oasis-Wonderwall - Remastered", "3472", "1", "(x1)", "137,742"]
        )
        assert entry["artist"] == "Oasis"
        assert entry["track"] == "Wonderwall - Remastered"

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


class TestSeparatorPrecedenceRegression:
    """kworb separates artist and title with a BARE hyphen, never " - ".

    Measured, not assumed: 100 labels from a full GB chart page, every one of
    the form 'Artist-Title'. A spaced " - " appears only inside a title, as a
    suffix - 'Oasis-Wonderwall - Remastered'.

    The parser used to try " - " first, so it split at the suffix and produced
    the artist 'Oasis-Wonderwall'. That name exists nowhere. It matched
    nothing in MusicBrainz or Deezer, so it sat in the enrichment backlog
    being re-queried every night forever, and it charted in 32 countries
    carrying 250M streams credited to no real artist - which silently
    understated domestic share in every one of them.

    Nothing errored. The row count was right, the gate passed, the page
    rendered. This is the third silent failure in this project and the
    largest: the others left a field blank, this one corrupted a fact.
    """

    def test_suffixed_title_does_not_steal_the_artist(self):
        # The exact production row, from data/raw/kworb/gb.json.
        assert parse_artist_from_chart_row("Oasis-Wonderwall - Remastered") == "Oasis"

    def test_ordinary_label(self):
        assert parse_artist_from_chart_row("The Goo Goo Dolls-Iris") == "The Goo Goo Dolls"

    def test_featured_artist_stays_on_the_title(self):
        # Trailing collaborators belong to the track, not the artist - the
        # chart credits the lead act and so do we.
        assert parse_artist_from_chart_row("Sam Fender-Rein Me In(w/Olivia Dean)") == "Sam Fender"

    def test_no_separator_is_the_whole_label(self):
        assert parse_artist_from_chart_row("Wxoda") == "Wxoda"

    def test_hyphenated_artist_is_still_truncated(self):
        """A known limitation, pinned so it is a decision and not a surprise.

        'Jay-Z' and 'Oasis-Wonderwall' are the same shape; no rule over the
        raw string tells them apart. This was equally true before the
        separator order changed - kworb never writes " - " between artist and
        title, so the spaced branch never protected hyphenated names. Fixing
        it needs an oracle, and mb_artist_names is now sitting there holding
        3.5M real names for exactly that job.
        """
        assert parse_artist_from_chart_row("Jay-Z-99 Problems") == "Jay"

    def test_empty_and_missing(self):
        assert parse_artist_from_chart_row(None) is None
        assert parse_artist_from_chart_row("   ") is None
