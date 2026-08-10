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
from collections import Counter

import pytest

from app.services.cleansing import (
    genre_document_frequency,
    merge_genre_signals,
    normalize_artist_name,
    normalize_genre,
    parse_artist_from_chart_row,
    score_distinctiveness,
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

    def test_default_returns_full_distribution_not_top_5(self):
        # Regression guard: this used to default to top_n=5, which threw away
        # the long tail that distinctiveness scoring depends on (India's
        # "bollywood" ranked 7th and was silently discarded).
        lastfm = {
            f"Artist {i}": [{"name": g, "count": 50}]
            for i, g in enumerate(
                ["pop", "rock", "jazz", "blues", "metal", "reggae", "soul"]
            )
        }
        assert len(merge_genre_signals(lastfm, {})) == 7


class TestParseArtistFromChartRow:
    def test_none_and_empty(self):
        assert parse_artist_from_chart_row(None) is None
        assert parse_artist_from_chart_row("   ") is None

    def test_spaced_separator(self):
        assert parse_artist_from_chart_row("Drake - Hotline Bling") == "Drake"

    def test_unspaced_separator(self):
        # Regression: run_cleanse only split on " - ", so these survived whole
        # and matched nothing in Deezer or MusicBrainz.
        assert parse_artist_from_chart_row("BTS-NORMAL") == "BTS"
        assert parse_artist_from_chart_row("ATEEZ-BAD") == "ATEEZ"

    def test_collaborator_suffix_in_song_title_is_dropped_with_the_song(self):
        assert (
            parse_artist_from_chart_row("Fuerza Regida-COQUETA(w/Grupo Frontera)")
            == "Fuerza Regida"
        )

    def test_spaced_separator_wins_so_hyphenated_names_survive(self):
        # " - " is tried first precisely so hyphenated artist names aren't
        # truncated when the row is well-formed.
        assert parse_artist_from_chart_row("Jay-Z - 99 Problems") == "Jay-Z"

    def test_row_with_no_separator_returned_as_is(self):
        assert parse_artist_from_chart_row("Radiohead") == "Radiohead"


class TestGenreDocumentFrequency:
    def test_counts_countries_not_occurrences(self):
        by_country = {
            "aa": [{"genre": "pop", "score": 50}, {"genre": "jazz", "score": 50}],
            "bb": [{"genre": "pop", "score": 100}],
        }
        df = genre_document_frequency(by_country)
        assert df["pop"] == 2
        assert df["jazz"] == 1

    def test_empty_input(self):
        assert genre_document_frequency({}) == Counter()

    def test_trace_presence_does_not_count_toward_document_frequency(self):
        # The bug this guards: with 100 artists sampled per country, one
        # stray tag put a genre in every country's distribution, driving
        # document frequency to N and collapsing every IDF weight to 0.
        # j-pop hit 20/20 and scored 0 for Japan while being 11 percent of
        # what Japan actually plays.
        by_country = {
            "jp": [{"genre": "j-pop", "score": 155}, {"genre": "rock", "score": 845}],
            "us": [{"genre": "j-pop", "score": 1}, {"genre": "rock", "score": 999}],
            "gb": [{"genre": "j-pop", "score": 2}, {"genre": "rock", "score": 998}],
        }
        df = genre_document_frequency(by_country)
        assert df["j-pop"] == 1  # only Japan listens to it meaningfully
        assert df["rock"] == 3

    def test_country_with_zero_total_weight_is_skipped(self):
        assert genre_document_frequency({"aa": [{"genre": "pop", "score": 0}]}) == Counter()


class TestScoreDistinctiveness:
    def test_universal_genre_scores_zero(self):
        # In every country -> log(N/N) = log(1) = 0. This is the whole point:
        # pop can't distinguish a country if everyone listens to it.
        rows = [{"genre": "pop", "score": 100, "sources": ["lastfm"]}]
        df = Counter({"pop": 20})
        result = score_distinctiveness(rows, df, total_countries=20)
        assert result[0]["distinctiveness"] == 0.0

    def test_rare_genre_outranks_more_popular_common_one(self):
        rows = [
            {"genre": "pop", "score": 100, "sources": ["lastfm"]},
            {"genre": "bollywood", "score": 20, "sources": ["lastfm"]},
        ]
        df = Counter({"pop": 20, "bollywood": 1})
        result = score_distinctiveness(rows, df, total_countries=20)
        # bollywood is 5x less played but unique, so it ranks first
        assert result[0]["genre"] == "bollywood"
        assert result[0]["distinctiveness"] > result[1]["distinctiveness"]

    def test_share_floor_blocks_promotion_of_thin_evidence(self):
        # Rarity alone isn't evidence. Japan had bossa nova - 5 of 1378
        # weight, 0.4 percent of its listening - ranked as its single most
        # distinctive genre purely because few other countries registered it.
        rows = [
            {"genre": "bossa nova", "score": 5, "sources": ["lastfm"]},
            {"genre": "rock", "score": 1373, "sources": ["lastfm"]},
        ]
        df = Counter({"bossa nova": 1, "rock": 20})
        result = score_distinctiveness(rows, df, total_countries=20)
        by_genre = {r["genre"]: r for r in result}
        assert by_genre["bossa nova"]["distinctiveness"] == 0.0

    def test_floor_is_a_share_so_it_survives_a_deeper_sample(self):
        # Same genre at the same 20 percent share scores identically whether
        # the country's weights total 100 or 10,000. An absolute floor would
        # silently stop filtering as sample depth grew.
        shallow = score_distinctiveness(
            [{"genre": "a", "score": 20, "sources": []},
             {"genre": "b", "score": 80, "sources": []}],
            Counter({"a": 1, "b": 20}), total_countries=20,
        )
        deep = score_distinctiveness(
            [{"genre": "a", "score": 2000, "sources": []},
             {"genre": "b", "score": 8000, "sources": []}],
            Counter({"a": 1, "b": 20}), total_countries=20,
        )
        assert shallow[0]["genre"] == deep[0]["genre"] == "a"
        # Loose tolerance: each weight is rounded to 3dp, so the same value at
        # 100x the magnitude rounds slightly differently. The point is that
        # the ranking and the scale track the input, not exact equality.
        assert deep[0]["distinctiveness"] == pytest.approx(
            shallow[0]["distinctiveness"] * 100, rel=1e-4
        )

    def test_original_fields_preserved(self):
        rows = [{"genre": "jazz", "score": 10, "sources": ["lastfm", "musicbrainz"]}]
        result = score_distinctiveness(rows, Counter({"jazz": 2}), total_countries=20)
        assert result[0]["score"] == 10
        assert result[0]["sources"] == ["lastfm", "musicbrainz"]

    def test_handles_zero_countries_without_dividing_by_zero(self):
        rows = [{"genre": "jazz", "score": 10, "sources": []}]
        result = score_distinctiveness(rows, Counter(), total_countries=0)
        assert result[0]["distinctiveness"] == 0.0
