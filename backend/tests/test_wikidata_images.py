"""Tests for the Wikidata artist-image fallback.

The network call itself isn't tested - the query building, response parsing
and thumbnail sizing are, since those are where the bugs live and they're
pure functions. The SPARQL response fixture below matches the shape
query.wikidata.org actually returns (bindings with typed value objects), so
parsing is exercised against the real contract rather than a convenient one.
"""
from app.services.extractors.wikidata import (
    DEFAULT_THUMBNAIL_WIDTH,
    build_sparql,
    parse_bindings,
    thumbnail_url,
)

RADIOHEAD_MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
COLDPLAY_MBID = "cc197bad-dc9c-440d-a5b5-d52ba2e14234"

_SPARQL_RESPONSE = {
    "results": {
        "bindings": [
            {
                "mbid": {"type": "literal", "value": RADIOHEAD_MBID},
                "image": {
                    "type": "uri",
                    "value": "http://commons.wikimedia.org/wiki/Special:FilePath/Radiohead.jpg",
                },
            },
            {
                "mbid": {"type": "literal", "value": COLDPLAY_MBID},
                "image": {
                    "type": "uri",
                    "value": "http://commons.wikimedia.org/wiki/Special:FilePath/Coldplay.jpg",
                },
            },
        ]
    }
}


class TestBuildSparql:
    def test_includes_every_mbid_as_a_values_literal(self):
        query = build_sparql([RADIOHEAD_MBID, COLDPLAY_MBID])
        assert f'"{RADIOHEAD_MBID}"' in query
        assert f'"{COLDPLAY_MBID}"' in query

    def test_joins_on_musicbrainz_id_and_image_properties(self):
        # P434 is the MusicBrainz artist ID, P18 the image. Joining on an
        # identifier is the whole point - name matching is what makes the
        # Deezer lookup return the wrong artist sometimes.
        query = build_sparql([RADIOHEAD_MBID])
        assert "wdt:P434" in query
        assert "wdt:P18" in query

    def test_empty_list_still_produces_valid_shaped_query(self):
        assert "VALUES" in build_sparql([])


class TestThumbnailUrl:
    def test_adds_width_parameter(self):
        url = thumbnail_url(
            "https://commons.wikimedia.org/wiki/Special:FilePath/A.jpg", width=250
        )
        assert url.endswith("?width=250")

    def test_upgrades_http_to_https(self):
        # Wikidata still emits http:// for P18; a mixed-content image is
        # blocked outright by the browser on an https page.
        url = thumbnail_url("http://commons.wikimedia.org/wiki/Special:FilePath/A.jpg")
        assert url.startswith("https://")
        assert "http://" not in url

    def test_uses_ampersand_when_url_already_has_a_query(self):
        url = thumbnail_url("https://example.org/File.jpg?foo=1", width=100)
        assert url.endswith("&width=100")


class TestParseBindings:
    def test_maps_mbid_to_sized_https_url(self):
        images = parse_bindings(_SPARQL_RESPONSE)
        assert set(images) == {RADIOHEAD_MBID, COLDPLAY_MBID}
        assert images[RADIOHEAD_MBID].startswith("https://")
        assert images[RADIOHEAD_MBID].endswith(f"?width={DEFAULT_THUMBNAIL_WIDTH}")

    def test_ignores_rows_missing_either_field(self):
        payload = {
            "results": {
                "bindings": [
                    {"mbid": {"value": RADIOHEAD_MBID}},  # no image
                    {"image": {"value": "http://example.org/x.jpg"}},  # no mbid
                ]
            }
        }
        assert parse_bindings(payload) == {}

    def test_first_image_wins_for_artists_with_several(self):
        # An artist can carry multiple P18 values; taking the first keeps
        # output stable between runs instead of flipping with result order.
        payload = {
            "results": {
                "bindings": [
                    {
                        "mbid": {"value": RADIOHEAD_MBID},
                        "image": {"value": "http://example.org/first.jpg"},
                    },
                    {
                        "mbid": {"value": RADIOHEAD_MBID},
                        "image": {"value": "http://example.org/second.jpg"},
                    },
                ]
            }
        }
        assert "first.jpg" in parse_bindings(payload)[RADIOHEAD_MBID]

    def test_empty_response(self):
        assert parse_bindings({}) == {}
        assert parse_bindings({"results": {"bindings": []}}) == {}
