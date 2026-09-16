"""Tests for the batched artist-enrichment queries.

Enrichment used to be one MusicBrainz request per artist at ~1 req/sec:
4,244 calls, ~106 minutes of pure waiting, ten weekly runs to finish. These
Wikidata queries answer the same question a few hundred artists at a time, so
what's worth pinning here is the query construction (a bad escape breaks the
whole batch, not one artist) and the ambiguity rule.
"""
import pytest

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


class TestAMissIsAResultNotAFailure:
    """The enrichment treadmill, fixed 2026-09-16.

    resolve_via_musicbrainz used to catch its own transport errors and return
    (None, {}) - the SAME value it returns when MusicBrainz searched and has no
    such artist. The caller could not tell them apart, assumed a network blip,
    and skipped the row. A permanent miss was therefore never recorded and was
    re-asked every night out of a 250-request budget, which is why the backlog
    went 1,591 -> 1,602 across two full runs instead of down by 500.

    An overloaded sentinel is a bug the caller cannot recover from: the
    information was destroyed at the boundary. Exceptions are the channel for
    "could not ask"; a return value is the channel for "asked, and here is the
    answer" - including when the answer is nothing.
    """

    def test_a_transport_failure_raises(self, monkeypatch):
        import requests

        from scripts import run_extract_artist_meta as meta

        def boom(name):
            raise requests.RequestException("connection reset")

        monkeypatch.setattr(meta.musicbrainz, "search_artist", boom)
        with pytest.raises(requests.RequestException):
            meta.resolve_via_musicbrainz("BTS", None)

    def test_a_genuine_miss_returns_a_value(self, monkeypatch):
        from scripts import run_extract_artist_meta as meta

        monkeypatch.setattr(meta.musicbrainz, "search_artist", lambda name: None)
        monkeypatch.setattr(meta.time, "sleep", lambda s: None)
        assert meta.resolve_via_musicbrainz("Nobody At All", None) == (None, {})

    def test_a_miss_is_recorded_as_looked_up(self):
        from scripts.run_extract_artist_meta import _row

        row = _row("Nobody At All", None, {}, "not_found")
        # resolved_at set is what takes it off the worklist; resolved_by is
        # what lets the re-check policy put it back after 90 days.
        assert row["resolved_at"] is not None
        assert row["resolved_by"] == "not_found"
        assert row["origin_country"] is None

    def test_provenance_distinguishes_the_paths(self):
        from scripts.run_extract_artist_meta import _row

        found = _row("BTS", "abc", {"country": "kr", "formed_year": 2013}, "api")
        assert found["resolved_by"] == "api"
        assert found["origin_country"] == "kr"


class TestTheWorklistIsBounded:
    """It asks for recently-charting artists, not the whole dimension.

    `artists` only ever gains rows, so iterating over it is iterating over
    everything that has ever charted. 390 of 1,030 unresolved artists had not
    charted anywhere in three days and were still being re-queried nightly.

    Asserted on the generated SQL rather than against BigQuery - there is no
    emulator, and these three clauses are the whole design.
    """

    def _sql(self, monkeypatch):
        from scripts import run_extract_artist_meta as meta

        seen = {}
        monkeypatch.setattr(meta, "dataset_id", lambda: "proj.ds")
        monkeypatch.setattr(
            meta, "run_query", lambda sql: seen.update(sql=sql) or []
        )
        meta.pending_artists()
        return seen["sql"]

    def test_joins_recent_chart_presence_rather_than_outer_joining(self, monkeypatch):
        sql = self._sql(monkeypatch)
        assert "JOIN recent r" in sql
        assert "LEFT JOIN" not in sql

    def test_orders_by_recent_rows_not_lifetime(self, monkeypatch):
        sql = self._sql(monkeypatch)
        assert "INTERVAL 7 DAY" in sql
        assert "ORDER BY r.chart_rows DESC" in sql

    def test_re_checks_expired_misses(self, monkeypatch):
        # Without this clause, recording a miss would be a permanent verdict
        # and an artist added to MusicBrainz later could never be picked up.
        sql = self._sql(monkeypatch)
        assert "resolved_by = 'not_found'" in sql
        assert "INTERVAL 90 DAY" in sql

    def test_still_includes_artists_never_looked_up(self, monkeypatch):
        sql = self._sql(monkeypatch)
        assert "a.resolved_at IS NULL" in sql
