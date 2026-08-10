"""Tests for Deezer image handling.

Deezer never returns an empty picture field - for artists it has no photo
of, it returns a well-formed CDN URL whose path is the MD5 of the empty
string. A plain truthiness check therefore accepts it and the UI renders a
blank square, which is what made Radiohead, Coldplay, The Weeknd and ~50
others appear broken.
"""
from app.services.extractors.deezer import has_real_picture, pick_picture

EMPTY_HASH_URL = (
    "https://cdn-images.dzcdn.net/images/artist/"
    "d41d8cd98f00b204e9800998ecf8427e/250x250-000000-80-0-0.jpg"
)
REAL_URL = (
    "https://cdn-images.dzcdn.net/images/artist/"
    "1051e7fd110f9d3e5e88cdc69c5f227b/250x250-000000-80-0-0.jpg"
)


class TestHasRealPicture:
    def test_none_and_empty(self):
        assert has_real_picture(None) is False
        assert has_real_picture("") is False

    def test_empty_hash_placeholder_rejected(self):
        assert has_real_picture(EMPTY_HASH_URL) is False

    def test_real_url_accepted(self):
        assert has_real_picture(REAL_URL) is True


class TestPickPicture:
    def test_prefers_medium(self):
        assert pick_picture({"picture_medium": REAL_URL, "picture_big": REAL_URL}) == REAL_URL

    def test_falls_back_to_big_when_medium_is_placeholder(self):
        # The placeholder is per-size, so an artist can have one and not the
        # other - falling back is worth doing rather than giving up on medium.
        assert pick_picture(
            {"picture_medium": EMPTY_HASH_URL, "picture_big": REAL_URL}
        ) == REAL_URL

    def test_returns_none_when_all_sizes_are_placeholders(self):
        assert pick_picture(
            {"picture_medium": EMPTY_HASH_URL, "picture_big": EMPTY_HASH_URL}
        ) is None

    def test_returns_none_for_empty_payload(self):
        assert pick_picture({}) is None
