"""Parsing the MusicBrainz dump, and the parsers it shares with the API path.

The dump and the web service return the same payload shape, which is the whole
premise of `run_import_mb_dump`: the same `parse_artist_meta` reads both. These
tests pin that premise down, because if the two paths ever produced different
rows for the same artist, the bug would show up as a country that changes
depending on which code path last touched it - about the worst diagnostic
signature available.

The payloads below are shaped like real `ws/2` artist responses. They are
deliberately awkward: a missing top-level country, a partial date, an artist
with no usable id.
"""
import io
import json
import tarfile

from app.services.extractors.musicbrainz import (
    parse_aliases,
    parse_artist_meta,
    parse_genres,
)
from scripts.run_import_mb_dump import artist_row, iter_records, name_rows

IMPORTED_AT = "2026-09-14T00:00:00+00:00"


class TestParseArtistMeta:
    def test_prefers_the_top_level_country(self):
        payload = {
            "country": "GB",
            "area": {"name": "London", "iso-3166-1-codes": ["XX"]},
        }
        assert parse_artist_meta(payload)["country"] == "gb"

    def test_falls_back_to_the_area_country_code(self):
        # MusicBrainz's `area` can be a city or region, so the ISO code on it
        # is the only usable country signal when `country` is absent - which
        # happens for a fair number of artists.
        payload = {"area": {"name": "Sweden", "iso-3166-1-codes": ["SE"]}}
        assert parse_artist_meta(payload)["country"] == "se"

    def test_no_country_anywhere_is_none_not_a_guess(self):
        assert parse_artist_meta({"area": {"name": "London"}})["country"] is None
        assert parse_artist_meta({})["country"] is None

    def test_lowercases_to_match_the_countries_seed(self):
        assert parse_artist_meta({"country": "TH"})["country"] == "th"

    def test_takes_the_year_from_a_partial_date(self):
        # Dates arrive as "1985", "1985-06" or "1985-06-21".
        for begin, expected in [("1985", 1985), ("1985-06", 1985), ("1985-06-21", 1985)]:
            assert parse_artist_meta({"life-span": {"begin": begin}})["formed_year"] == expected

    def test_unparseable_or_missing_dates_are_none(self):
        assert parse_artist_meta({"life-span": {"begin": "????"}})["formed_year"] is None
        assert parse_artist_meta({"life-span": {}})["formed_year"] is None
        assert parse_artist_meta({})["formed_year"] is None


class TestParseGenresAndAliases:
    def test_genres_keep_name_and_vote_count(self):
        payload = {"genres": [{"name": "k-pop", "count": 12, "id": "x"}]}
        assert parse_genres(payload) == [{"name": "k-pop", "count": 12}]

    def test_no_genres_is_an_empty_list(self):
        assert parse_genres({}) == []

    def test_aliases_include_the_primary_name_first(self):
        payload = {"name": "BTS", "aliases": [{"name": "방탄소년단"}, {"name": "Bangtan Boys"}]}
        assert parse_aliases(payload) == ["BTS", "방탄소년단", "Bangtan Boys"]

    def test_duplicate_aliases_collapse(self):
        payload = {"name": "BTS", "aliases": [{"name": "BTS"}, {"name": "BTS"}]}
        assert parse_aliases(payload) == ["BTS"]

    def test_empty_alias_names_are_dropped(self):
        payload = {"name": "BTS", "aliases": [{"name": None}, {}]}
        assert parse_aliases(payload) == ["BTS"]


class TestArtistRow:
    def test_maps_a_full_record(self):
        payload = {
            "id": "mbid-1",
            "name": "Robyn",
            "sort-name": "Robyn",
            "country": "SE",
            "type": "Person",
            "life-span": {"begin": "1979-06-12"},
        }
        row = artist_row(payload, IMPORTED_AT)
        assert row == {
            "mbid": "mbid-1",
            "name": "Robyn",
            "sort_name": "Robyn",
            "country": "se",
            "formed_year": 1979,
            "artist_type": "Person",
            "imported_at": IMPORTED_AT,
        }

    def test_a_record_without_an_id_is_skipped_not_half_written(self):
        # A row with a NULL key would join to nothing and sit in the mirror
        # forever looking like data.
        assert artist_row({"name": "Nameless"}, IMPORTED_AT) is None
        assert artist_row({"id": "mbid-2"}, IMPORTED_AT) is None


class TestNameRows:
    def test_one_row_per_distinct_normalised_name(self):
        payload = {"id": "mbid-1", "name": "BTS", "aliases": [{"name": "Bangtan Boys"}]}
        rows = name_rows(payload)
        assert {r["match_name"] for r in rows} == {"bts", "bangtan boys"}
        assert all(r["mbid"] == "mbid-1" for r in rows)

    def test_only_the_artists_own_name_is_primary(self):
        payload = {"id": "mbid-1", "name": "BTS", "aliases": [{"name": "Bangtan Boys"}]}
        primary = [r["match_name"] for r in name_rows(payload) if r["is_primary"]]
        assert primary == ["bts"]

    def test_aliases_that_normalise_onto_the_primary_do_not_duplicate_it(self):
        # "BTS" and "bts" are the same match key; emitting both would make the
        # join return two rows for one artist and double any count built on it.
        payload = {"id": "mbid-1", "name": "BTS", "aliases": [{"name": "bts"}]}
        assert len(name_rows(payload)) == 1

    def test_no_id_yields_nothing(self):
        assert name_rows({"name": "BTS"}) == []


def _make_dump(records: list[dict]) -> io.BytesIO:
    """A tar.xz shaped like the published dump: metadata plus one data file."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tar:
        for name, body in [
            ("mbdump/TIMESTAMP", b"2026-09-14 00:00:00\n"),
            ("mbdump/COPYING", b"CC0\n"),
            (
                "mbdump/artist",
                b"".join(json.dumps(r).encode() + b"\n" for r in records),
            ),
        ]:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    buf.seek(0)
    return buf


class TestIterRecords:
    def test_reads_records_and_ignores_the_metadata_members(self):
        dump = _make_dump([{"id": "a"}, {"id": "b"}])
        assert [r["id"] for r in iter_records(dump)] == ["a", "b"]

    def test_blank_lines_are_skipped(self):
        buf = io.BytesIO()
        body = b'{"id": "a"}\n\n{"id": "b"}\n'
        with tarfile.open(fileobj=buf, mode="w:xz") as tar:
            info = tarfile.TarInfo("mbdump/artist")
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
        buf.seek(0)
        assert [r["id"] for r in iter_records(buf)] == ["a", "b"]

    def test_a_malformed_line_raises_rather_than_being_dropped(self):
        # Silently skipping bad lines in a reference import is how a join
        # quietly loses a percent of its rows with nothing in the logs.
        buf = io.BytesIO()
        body = b'{"id": "a"}\nnot json\n'
        with tarfile.open(fileobj=buf, mode="w:xz") as tar:
            info = tarfile.TarInfo("mbdump/artist")
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
        buf.seek(0)
        try:
            list(iter_records(buf))
        except json.JSONDecodeError:
            return
        raise AssertionError("expected a JSONDecodeError")


class TestSharedParsing:
    def test_the_dump_and_api_paths_agree(self):
        """The premise of the whole import: same payload, same row.

        `get_artist_meta` is a thin wrapper over `parse_artist_meta`, so a
        dump record and an API response for the same artist must produce
        identical fields. If this ever fails, the two paths have diverged and
        an artist's country would depend on which one last ran.
        """
        payload = {
            "id": "mbid-1",
            "name": "Robyn",
            "area": {"name": "Sweden", "iso-3166-1-codes": ["SE"]},
            "life-span": {"begin": "1979"},
        }
        from_dump = artist_row(payload, IMPORTED_AT)
        from_api = parse_artist_meta(payload)
        assert from_dump["country"] == from_api["country"]
        assert from_dump["formed_year"] == from_api["formed_year"]
