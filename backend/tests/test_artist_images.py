"""Tests for how the API resolves an artist's image across two sources.

Deezer is primary and Wikidata fills its gaps, so the things worth pinning
down are the precedence rule and - importantly - that "Deezer has a photo" is
judged by deezer.pick_picture rather than by truthiness. Deezer returns a
well-formed placeholder URL for artists it has no photo of, so a naive check
concludes it has one and the fallback never fires for exactly the artists
that need it.
"""
import importlib
import json

import pytest

EMPTY_HASH = "d41d8cd98f00b204e9800998ecf8427e"
PLACEHOLDER = f"https://cdn-images.dzcdn.net/images/artist/{EMPTY_HASH}/250x250.jpg"
REAL_DEEZER = "https://cdn-images.dzcdn.net/images/artist/1051e7fd110f9d3e5/250x250.jpg"
COMMONS = "https://commons.wikimedia.org/wiki/Special:FilePath/Radiohead.jpg?width=250"


@pytest.fixture()
def images_module(tmp_path, monkeypatch):
    """countries.py with both image files redirected into a temp dir.

    The module caches on file mtimes, so it's reloaded per test to start from
    a clean cache rather than one primed by a previous test's paths.
    """
    import app.services.countries as countries

    countries = importlib.reload(countries)
    deezer_path = tmp_path / "deezer.json"
    wikidata_path = tmp_path / "wikidata.json"
    monkeypatch.setattr(countries, "DEEZER_ARTISTS_PATH", deezer_path)
    monkeypatch.setattr(countries, "WIKIDATA_ARTISTS_PATH", wikidata_path)
    monkeypatch.setattr(countries, "_image_cache", None)
    return countries, deezer_path, wikidata_path


def _write(path, payload):
    path.write_text(json.dumps(payload))


class TestArtistImageResolution:
    def test_no_files_at_all(self, images_module):
        countries, _, _ = images_module
        assert countries._artist_images() == {}

    def test_deezer_wins_when_it_has_a_real_photo(self, images_module):
        countries, deezer_path, wikidata_path = images_module
        _write(deezer_path, {"Drake": {"picture_medium": REAL_DEEZER}})
        _write(wikidata_path, {"Drake": {"mbid": "x", "image": COMMONS}})
        assert countries._artist_images()["Drake"] == REAL_DEEZER

    def test_wikidata_fills_a_deezer_placeholder(self, images_module):
        # The Radiohead case: Deezer "has" a URL, but it's the empty-hash
        # placeholder that renders blank.
        countries, deezer_path, wikidata_path = images_module
        _write(deezer_path, {"Radiohead": {"picture_medium": PLACEHOLDER}})
        _write(wikidata_path, {"Radiohead": {"mbid": "x", "image": COMMONS}})
        assert countries._artist_images()["Radiohead"] == COMMONS

    def test_wikidata_fills_an_artist_deezer_never_returned(self, images_module):
        countries, deezer_path, wikidata_path = images_module
        _write(deezer_path, {})
        _write(wikidata_path, {"Tool": {"mbid": "x", "image": COMMONS}})
        assert countries._artist_images()["Tool"] == COMMONS

    def test_artist_missing_from_both_has_no_entry(self, images_module):
        countries, deezer_path, wikidata_path = images_module
        _write(deezer_path, {"Ghost": {"picture_medium": PLACEHOLDER}})
        _write(wikidata_path, {})
        assert "Ghost" not in countries._artist_images()

    def test_malformed_wikidata_rows_are_skipped(self, images_module):
        countries, deezer_path, wikidata_path = images_module
        _write(deezer_path, {})
        _write(wikidata_path, {"A": None, "B": {"mbid": "x"}, "C": {"image": COMMONS}})
        assert countries._artist_images() == {"C": COMMONS}

    def test_cache_refreshes_when_a_file_changes(self, images_module):
        # The pipeline rewrites these files on every run; a cache that never
        # noticed would serve the startup snapshot forever in production.
        countries, deezer_path, wikidata_path = images_module
        _write(deezer_path, {"Drake": {"picture_medium": REAL_DEEZER}})
        _write(wikidata_path, {})
        assert "Drake" in countries._artist_images()

        import os
        _write(deezer_path, {"Drake": {"picture_medium": PLACEHOLDER}})
        stat = deezer_path.stat()
        os.utime(deezer_path, (stat.st_atime + 10, stat.st_mtime + 10))

        assert "Drake" not in countries._artist_images()


class TestArtistsNeedingImages:
    def test_selects_placeholder_and_missing_artists_only(self, tmp_path, monkeypatch):
        from scripts import run_extract_wikidata as script

        deezer_path = tmp_path / "deezer.json"
        _write(
            deezer_path,
            {
                "HasPhoto": {"picture_medium": REAL_DEEZER},
                "Placeholder": {"picture_medium": PLACEHOLDER},
            },
        )
        monkeypatch.setattr(script, "DEEZER_ARTISTS_PATH", deezer_path)

        needed = script.artists_needing_images(
            {"HasPhoto": "m1", "Placeholder": "m2", "NeverReturned": "m3"}
        )
        assert set(needed) == {"Placeholder", "NeverReturned"}

    def test_no_deezer_file_means_everything_needs_an_image(self, tmp_path, monkeypatch):
        from scripts import run_extract_wikidata as script

        monkeypatch.setattr(script, "DEEZER_ARTISTS_PATH", tmp_path / "missing.json")
        assert script.artists_needing_images({"A": "m1"}) == {"A": "m1"}
