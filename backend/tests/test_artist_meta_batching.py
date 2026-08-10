"""Tests for the batched artist-enrichment queries.

Enrichment used to be one MusicBrainz request per artist at ~1 req/sec:
4,244 calls, ~106 minutes of pure waiting, ten weekly runs to finish. These
Wikidata queries answer the same question a few hundred artists at a time, so
what's worth pinning here is the query construction (a bad escape breaks the
whole batch, not one artist) and the ambiguity rule.
"""
from app.services.extractors.wikidata import (
    build_meta_by_mbid_sparql,
    build_meta_by_name_sparql,
    parse_meta_by_mbid,
    parse_meta_by_name,
)

MBID_A = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
MBID_B = "cc197bad-dc9c-440d-a5b5-d52ba2e14234"


def _binding(**kw):
    return {k: {"value": v} for k, v in kw.items()}


class TestQueryBuilding:
    def test_mbid_batch_includes_every_id(self):
        query = build_meta_by_mbid_sparql([MBID_A, MBID_B])
        assert f'"{MBID_A}"' in query and f'"{MBID_B}"' in query
        # One VALUES block = one request for the whole batch, which is the
        # entire point of this over MusicBrainz.
        assert query.count("VALUES") == 1

    def test_queries_the_right_properties(self):
        query = build_meta_by_mbid_sparql([MBID_A])
        assert "wdt:P434" in query  # MusicBrainz id
        assert "wdt:P495" in query  # country of origin (bands)
        assert "wdt:P27" in query   # country of citizenship (people)
        assert "wdt:P297" in query  # ISO alpha-2
        assert "wdt:P571" in query  # inception

    def test_name_batch_escapes_quotes(self):
        # An unescaped quote would break the whole batch, not one artist.
        query = build_meta_by_name_sparql(['"Weird Al" Yankovic'])
        assert '\\"Weird Al\\" Yankovic' in query

    def test_name_batch_escapes_backslashes(self):
        query = build_meta_by_name_sparql(["AC\\DC"])
        assert "AC\\\\DC" in query

    def test_name_batch_restricts_to_musicians_and_groups(self):
        # Bare labels collide constantly - "Alaska" is a US state and a
        # Spanish singer - so the type filter isn't optional.
        query = build_meta_by_name_sparql(["Alaska"])
        assert "wd:Q639669" in query   # musician (occupation)
        assert "wd:Q2088357" in query  # musical ensemble

    def test_empty_batches_are_still_valid_shaped(self):
        assert "VALUES" in build_meta_by_mbid_sparql([])
        assert "VALUES" in build_meta_by_name_sparql([])


class TestParseByMbid:
    def test_extracts_country_and_year(self):
        payload = {"results": {"bindings": [
            _binding(mbid=MBID_A, iso="GB", inception="1985-01-01T00:00:00Z"),
        ]}}
        assert parse_meta_by_mbid(payload) == {
            MBID_A: {"country": "gb", "formed_year": 1985}
        }

    def test_country_lowercased_to_match_our_codes(self):
        payload = {"results": {"bindings": [_binding(mbid=MBID_A, iso="JP")]}}
        assert parse_meta_by_mbid(payload)[MBID_A]["country"] == "jp"

    def test_first_value_wins_for_multiple_citizenships(self):
        # Artists routinely hold several; taking the first keeps reruns
        # stable instead of flipping with result order.
        payload = {"results": {"bindings": [
            _binding(mbid=MBID_A, iso="GB"),
            _binding(mbid=MBID_A, iso="US"),
        ]}}
        assert parse_meta_by_mbid(payload)[MBID_A]["country"] == "gb"

    def test_missing_fields_are_none_not_absent(self):
        payload = {"results": {"bindings": [_binding(mbid=MBID_A)]}}
        assert parse_meta_by_mbid(payload)[MBID_A] == {
            "country": None,
            "formed_year": None,
        }

    def test_empty_payload(self):
        assert parse_meta_by_mbid({}) == {}


class TestParseByName:
    def test_unambiguous_name_resolves(self):
        payload = {"results": {"bindings": [
            _binding(name="Radiohead", item="http://wikidata.org/Q1", iso="GB"),
        ]}}
        assert parse_meta_by_name(payload)["Radiohead"]["country"] == "gb"

    def test_ambiguous_name_is_dropped_entirely(self):
        # Two different entities share the label. Guessing would attribute
        # one artist's nationality to another - a wrong country silently
        # corrupts the domestic-share figure, whereas a missing one only
        # lowers its coverage.
        payload = {"results": {"bindings": [
            _binding(name="Alaska", item="http://wikidata.org/Q1", iso="US"),
            _binding(name="Alaska", item="http://wikidata.org/Q2", iso="ES"),
        ]}}
        assert parse_meta_by_name(payload) == {}

    def test_same_entity_twice_is_not_ambiguous(self):
        # Multiple rows for one item (several citizenships) must not be
        # mistaken for two different artists.
        payload = {"results": {"bindings": [
            _binding(name="Drake", item="http://wikidata.org/Q1", iso="CA"),
            _binding(name="Drake", item="http://wikidata.org/Q1", iso="US"),
        ]}}
        assert parse_meta_by_name(payload)["Drake"]["country"] == "ca"

    def test_rows_missing_name_or_item_are_skipped(self):
        payload = {"results": {"bindings": [
            _binding(name="X"),
            _binding(item="http://wikidata.org/Q9"),
        ]}}
        assert parse_meta_by_name(payload) == {}
