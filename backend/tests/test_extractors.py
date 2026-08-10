"""Tests for the extraction layer.

These matter more than their line count suggests. Extraction is the only part
of the pipeline whose input is controlled by someone else, and it fails
*silently*: kworb changing its markup, or Last.fm renaming a field, yields
zero rows rather than an error, and the first symptom is an empty dashboard
days later. The cleansing and warehouse layers are well covered; this was the
gap.

No network. `requests.get` is monkeypatched with fixtures shaped like the real
responses - the kworb HTML below reproduces the quirks of an actual chart page
(a summary table before the chart table, the unspaced "Artist-Song" cell
format, comma-grouped numbers).
"""
import pytest
import requests

from app.services.extractors import deezer, kworb, lastfm, musicbrainz


class FakeResponse:
    def __init__(self, *, json_data=None, content=b"", status_code=200, headers=None):
        self._json = json_data
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


# Shaped like a real kworb chart table: comma-grouped numbers, a "(xN)" weeks
# column, and the unspaced "Artist-Song" cell that
# cleansing.parse_artist_from_chart_row exists to split.
KWORB_HTML = b"""
<html><body>
<table class="chart">
  <tr><th>Pos</th><th>P+</th><th>Artist and Title</th><th>Days</th><th>Streams</th></tr>
  <tr><td>1</td><td>=</td><td>Ella Langley-Choosin' Texas</td><td>296</td><td>1,608,958</td></tr>
  <tr><td>2</td><td>+1</td><td>Malcolm Todd-Earrings</td><td>217</td><td>1,112,552</td></tr>
</table>
</body></html>
"""

# The scraper binds to the FIRST table in the document. kworb currently puts
# the chart first, so this works - but nothing enforces that, and a layout
# change that inserts any table above it would silently yield junk rows
# instead of an error. That fragility is what the test below documents.
KWORB_HTML_WITH_LEADING_TABLE = b"""
<html><body>
<table class="summary"><tr><th>Total</th></tr><tr><td>ignore me</td></tr></table>
""" + KWORB_HTML


class TestKworbScraper:
    def test_parses_chart_rows(self, monkeypatch):
        monkeypatch.setattr(
            requests, "get", lambda *a, **k: FakeResponse(content=KWORB_HTML)
        )
        rows = kworb.scrape_country_chart("us")
        assert len(rows) == 2
        assert rows[0] == ["1", "=", "Ella Langley-Choosin' Texas", "296", "1,608,958"]

    def test_binds_to_the_first_table_which_is_a_known_fragility(self, monkeypatch):
        # Not asserting desired behaviour - asserting ACTUAL behaviour, so the
        # risk is visible. find("table") takes whatever table appears first,
        # so a layout change above the chart yields junk rows silently rather
        # than failing. If this test ever starts mattering in production,
        # the fix is to select the chart table by class instead.
        monkeypatch.setattr(
            requests,
            "get",
            lambda *a, **k: FakeResponse(content=KWORB_HTML_WITH_LEADING_TABLE),
        )
        assert kworb.scrape_country_chart("us") == [["ignore me"]]

    def test_header_row_is_skipped(self, monkeypatch):
        html = b"<table><tr><th>Pos</th></tr><tr><td>1</td><td>Artist-Song</td></tr></table>"
        monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(content=html))
        assert kworb.scrape_country_chart("us") == [["1", "Artist-Song"]]

    def test_cells_are_stripped(self, monkeypatch):
        html = (
            b"<table><tr><th>h</th></tr>"
            b"<tr><td>  1  </td><td>\n Drake-Hotline \n</td></tr></table>"
        )
        monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(content=html))
        assert kworb.scrape_country_chart("us") == [["1", "Drake-Hotline"]]

    def test_no_table_returns_empty_rather_than_raising(self, monkeypatch):
        # kworb serves a normal page for unknown country codes. Returning []
        # lets the pipeline carry on with the other 75 countries.
        monkeypatch.setattr(
            requests, "get", lambda *a, **k: FakeResponse(content=b"<html><p>nope</p></html>")
        )
        assert kworb.scrape_country_chart("zz") == []

    def test_http_error_propagates(self, monkeypatch):
        monkeypatch.setattr(
            requests, "get", lambda *a, **k: FakeResponse(content=b"", status_code=503)
        )
        with pytest.raises(requests.HTTPError):
            kworb.scrape_country_chart("us")

    def test_requests_the_right_url_with_a_user_agent(self, monkeypatch):
        seen = {}

        def capture(url, **kwargs):
            seen["url"] = url
            seen["headers"] = kwargs.get("headers")
            return FakeResponse(content=KWORB_HTML)

        monkeypatch.setattr(requests, "get", capture)
        kworb.scrape_country_chart("gb")
        assert seen["url"] == "https://kworb.net/spotify/country/gb_daily.html"
        assert "User-Agent" in seen["headers"]


class TestLastfmExtractor:
    def test_keeps_only_the_fields_the_pipeline_uses(self, monkeypatch):
        payload = {
            "topartists": {
                "artist": [
                    {
                        "name": "Radiohead",
                        "listeners": "123",
                        "mbid": "a74b1b7f",
                        # Dropped on purpose: Last.fm's image field has
                        # returned the same placeholder for every artist
                        # since 2019, and streamable is always 0.
                        "image": [{"#text": "placeholder"}],
                        "streamable": "0",
                    }
                ]
            }
        }
        monkeypatch.setattr(
            requests, "get", lambda *a, **k: FakeResponse(json_data=payload)
        )
        assert lastfm.get_top_artists("key", "United Kingdom") == [
            {"name": "Radiohead", "listeners": "123", "mbid": "a74b1b7f"}
        ]

    def test_missing_topartists_key_returns_empty(self, monkeypatch):
        # What an unrecognised country name actually produces: HTTP 200 with
        # no artists, no error. This is the South Korea failure mode.
        monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(json_data={}))
        assert lastfm.get_top_artists("key", "Atlantis") == []

    def test_passes_country_and_limit_through(self, monkeypatch):
        seen = {}

        def capture(url, **kwargs):
            seen.update(kwargs.get("params", {}))
            return FakeResponse(json_data={})

        monkeypatch.setattr(requests, "get", capture)
        lastfm.get_top_artists("mykey", "Korea, Republic of", limit=100)
        assert seen["country"] == "Korea, Republic of"
        assert seen["limit"] == 100
        assert seen["api_key"] == "mykey"
        assert seen["method"] == "geo.gettopartists"

    def test_tags_are_reduced_to_name_and_count(self, monkeypatch):
        payload = {
            "toptags": {
                "tag": [{"name": "rock", "count": 100, "url": "https://drop.me"}]
            }
        }
        monkeypatch.setattr(
            requests, "get", lambda *a, **k: FakeResponse(json_data=payload)
        )
        assert lastfm.get_top_tags("key", "Radiohead") == [
            {"name": "rock", "count": 100}
        ]


class TestMusicbrainzRetries:
    @pytest.fixture(autouse=True)
    def no_sleeping(self, monkeypatch):
        monkeypatch.setattr(musicbrainz.time, "sleep", lambda *_: None)

    def test_retries_a_503_then_succeeds(self, monkeypatch):
        responses = [
            FakeResponse(status_code=503, headers={"Retry-After": "1"}),
            FakeResponse(json_data={"artists": [{"id": "mbid-1"}]}),
        ]
        monkeypatch.setattr(requests, "get", lambda *a, **k: responses.pop(0))
        assert musicbrainz.search_artist("Radiohead") == "mbid-1"

    def test_retry_after_zero_does_not_cause_a_zero_second_wait(self, monkeypatch):
        # Observed in the wild: MusicBrainz sends "Retry-After: 0" under load,
        # and honouring it literally produces a tight loop that just 503s
        # again. The wait is floored regardless of the header.
        waits = []
        monkeypatch.setattr(musicbrainz.time, "sleep", waits.append)
        responses = [
            FakeResponse(status_code=503, headers={"Retry-After": "0"}),
            FakeResponse(json_data={"artists": []}),
        ]
        monkeypatch.setattr(requests, "get", lambda *a, **k: responses.pop(0))
        musicbrainz.search_artist("Nobody")
        assert waits and all(w >= 2 for w in waits)

    def test_gives_up_after_max_retries(self, monkeypatch):
        monkeypatch.setattr(
            requests,
            "get",
            lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("down")),
        )
        with pytest.raises(requests.RequestException):
            musicbrainz.search_artist("Radiohead")

    def test_no_search_result_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            requests, "get", lambda *a, **k: FakeResponse(json_data={"artists": []})
        )
        assert musicbrainz.search_artist("Nonexistent") is None

    def test_genres_reduced_to_name_and_count(self, monkeypatch):
        payload = {"genres": [{"name": "rock", "count": 5, "disambiguation": "drop"}]}
        monkeypatch.setattr(
            requests, "get", lambda *a, **k: FakeResponse(json_data=payload)
        )
        assert musicbrainz.get_genres("mbid") == [{"name": "rock", "count": 5}]

    def test_artist_with_no_genres_is_normal_not_an_error(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(json_data={}))
        assert musicbrainz.get_genres("mbid") == []


class TestDeezerExtractor:
    def test_returns_first_match(self, monkeypatch):
        payload = {"data": [{"id": 1, "name": "Drake"}, {"id": 2, "name": "Drake Bell"}]}
        monkeypatch.setattr(
            requests, "get", lambda *a, **k: FakeResponse(json_data=payload)
        )
        assert deezer.search_artist("Drake")["id"] == 1

    def test_no_match_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            requests, "get", lambda *a, **k: FakeResponse(json_data={"data": []})
        )
        assert deezer.search_artist("zzzz") is None
