"""Unit tests for app.services.cleansing.

Runs against the real seed files (seeds/musicbrainz_genres.txt,
seeds/genre_buckets.txt), not mocks - these functions exist entirely to
handle real-world messy genre text, so the tests should exercise the real
taxonomy they'll actually run against in production, not a fake stand-in
that could drift from it and hide bugs.

Several of these tests pin down bugs that were actually hit while building
this pipeline (see comments) rather than being purely speculative - that's
deliberate. A regression test is only as good as the bug it remembers.
"""
from app.services.cleansing import (
    merge_genre_signals,
    normalize_artist_name,
    normalize_genre,
)


class TestNormalizeGenre:
    def test_none_and_empty_input_returns_none(self):
        assert normalize_genre(None) is None
        assert normalize_genre("") is None
        assert normalize_genre("   ") is None

    def test_lowercases_and_trims(self):
        assert normalize_genre("  Pop  ") == "pop"

    def test_alias_dict_resolves_semantic_synonyms(self):
        # "rap"/"trap" aren't textually similar to "hip-hop" at all - only
        # the alias dict, not fuzzy matching, connects these. The exact
        # final spelling still passes through fuzzy-matching against
        # MusicBrainz's canonical list afterward (see the convergence test
        # below), so assert equivalence rather than a hardcoded spelling
        # that could drift if the canonical list changes.
        assert normalize_genre("rap") == normalize_genre("hip-hop")
        assert normalize_genre("trap") == normalize_genre("hip-hop")
        assert normalize_genre("r&b") == "rnb"

    def test_alias_output_and_raw_spelling_converge(self):
        # Regression test: "hiphop" (-> alias "hip-hop") and a raw tag
        # already spelled "hip-hop" used to land on two different final
        # strings, because only un-aliased input got fuzzy-matched against
        # MusicBrainz's canonical spelling. Both paths must agree now.
        assert normalize_genre("hiphop") == normalize_genre("hip-hop")

    def test_decade_tags_are_junk(self):
        assert normalize_genre("80s") is None
        assert normalize_genre("2010s") is None
        assert normalize_genre("90") is None

    def test_very_short_tags_are_junk(self):
        assert normalize_genre("uk") is None  # len <= 2

    def test_unknown_genre_passes_through_lowercased(self):
        # Not in the alias dict and not close enough to fuzzy-match onto
        # anything in the canonical list - should survive as-is rather
        # than being silently dropped.
        result = normalize_genre("ThisIsNotARealGenreXYZ")
        assert result == "thisisnotarealgenrexyz"


class TestNormalizeArtistName:
    def test_none_and_empty_returns_none(self):
        assert normalize_artist_name(None) is None
        assert normalize_artist_name("   ") is None

    def test_strips_whitespace(self):
        assert normalize_artist_name("  Drake  ") == "Drake"

    def test_splits_on_featuring_markers(self):
        assert normalize_artist_name("Drake feat. Rihanna") == "Drake"
        assert normalize_artist_name("Drake ft. Rihanna") == "Drake"
        assert normalize_artist_name("Drake featuring Rihanna") == "Drake"
        assert normalize_artist_name("Drake with Rihanna") == "Drake"

    def test_splits_on_ampersand_and_comma(self):
        assert normalize_artist_name("Simon & Garfunkel") == "Simon"
        assert normalize_artist_name("A, B, C") == "A"

    def test_single_artist_unaffected(self):
        assert normalize_artist_name("Radiohead") == "Radiohead"


class TestMergeGenreSignals:
    def test_both_sources_vote_into_same_bucket(self):
        lastfm = {"Artist A": [{"name": "hip hop", "count": 80}]}
        musicbrainz = {"Artist A": ["hip-hop"]}
        result = merge_genre_signals(lastfm, musicbrainz)
        assert len(result) == 1
        assert result[0]["genre"] == "hip-hop"
        assert sorted(result[0]["sources"]) == ["lastfm", "musicbrainz"]

    def test_top_n_limits_results(self):
        lastfm = {
            "A": [{"name": "pop", "count": 100}],
            "B": [{"name": "rock", "count": 100}],
            "C": [{"name": "jazz", "count": 100}],
        }
        result = merge_genre_signals(lastfm, {}, top_n=2)
        assert len(result) == 2

    def test_results_sorted_highest_score_first(self):
        lastfm = {
            "A": [{"name": "pop", "count": 100}],
            "B": [{"name": "pop", "count": 100}],
            "C": [{"name": "rock", "count": 20}],
        }
        result = merge_genre_signals(lastfm, {})
        assert result[0]["genre"] == "pop"
        assert result[0]["score"] > result[1]["score"]

    def test_unclassifiable_tags_do_not_appear_in_results(self):
        lastfm = {"A": [{"name": "80s", "count": 100}]}  # junk, filtered pre-bucket
        result = merge_genre_signals(lastfm, {})
        assert result == []

    def test_empty_input_returns_empty_list(self):
        assert merge_genre_signals({}, {}) == []

    def test_stats_param_is_optional_and_backward_compatible(self):
        # No stats dict passed - should behave exactly as before.
        result = merge_genre_signals({"A": [{"name": "pop", "count": 50}]}, {})
        assert result[0]["genre"] == "pop"

    def test_stats_tracks_total_and_unclassified_counts(self):
        lastfm = {
            "A": [{"name": "pop", "count": 100}, {"name": "80s", "count": 100}],
        }
        stats: dict = {}
        merge_genre_signals(lastfm, {}, stats=stats)
        assert stats["total_tags"] == 2
        assert stats["unclassified_tags"] == 1
        assert stats["unclassified_rate"] == 0.5

    def test_stats_rate_is_zero_when_no_tags_seen(self):
        stats: dict = {}
        merge_genre_signals({}, {}, stats=stats)
        assert stats == {
            "total_tags": 0,
            "unclassified_tags": 0,
            "unclassified_rate": 0.0,
        }
