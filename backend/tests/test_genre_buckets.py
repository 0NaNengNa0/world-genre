"""Unit tests for app.services.genre_buckets.bucket_genre.

Runs against the real seeds/genre_buckets.txt taxonomy - see the note at
the top of test_cleansing.py for why these tests don't mock the seed data.
"""
from app.services.genre_buckets import bucket_genre


class TestBucketGenre:
    def test_none_and_empty_returns_other(self):
        assert bucket_genre(None) == "other"
        assert bucket_genre("") == "other"
        assert bucket_genre("   ") == "other"

    def test_exact_bucket_match(self):
        assert bucket_genre("pop") == "pop"
        assert bucket_genre("metal") == "metal"

    def test_case_insensitive(self):
        assert bucket_genre("Pop") == "pop"
        assert bucket_genre("HEAVY METAL") in {"metal", "heavy metal"}

    def test_longest_substring_wins_over_shorter_generic_bucket(self):
        # "death metal" should NOT get flattened to the broader "metal"
        # bucket if "death metal" itself is a bucket - longest-match-first
        # is what guarantees that.
        result = bucket_genre("death metal")
        assert result == "death metal"

    def test_keyword_rule_catches_unlisted_variant(self):
        # "chicago drill" isn't its own bucket entry, but the "drill"
        # keyword rule should route it to the "drill" bucket rather than
        # "other".
        assert bucket_genre("chicago drill") == "drill"

    def test_hip_hop_family_keyword_rules(self):
        # "gangster rap" (note: not "gangsta rap", which is itself a
        # curated bucket and would hit the substring tier instead) has no
        # bucket name as a substring, so only the "rap" keyword rule can
        # route it to "hip-hop".
        assert bucket_genre("gangster rap") == "hip-hop"
        assert bucket_genre("uk grime") == "grime"

    def test_electronic_family_keyword_rules(self):
        # "breakbeat" isn't itself a curated bucket and contains no bucket
        # name as a substring (unlike "deep house"/"acid techno", which
        # would hit the substring tier via "house"/"techno" first) - so
        # this specifically exercises the keyword-rule tier.
        assert bucket_genre("uk breakbeat") == "electronic"

    def test_substring_tier_beats_keyword_tier_for_own_buckets(self):
        # "house" and "techno" are themselves curated buckets - genres
        # containing them as a substring should resolve to the specific
        # bucket, not get flattened to the generic "electronic"
        # keyword-rule bucket. ("deep house" is itself an exact bucket
        # entry, so it doesn't exercise the substring tier - use variants
        # that aren't in the curated list themselves.)
        assert bucket_genre("tech house") == "house"
        assert bucket_genre("minimal techno") == "techno"

    def test_genuinely_unclassifiable_genre_falls_back_to_other(self):
        result = bucket_genre("zzz-not-a-real-genre-qqq")
        assert result == "other"

    def test_always_returns_a_string_never_none(self):
        for value in [None, "", "some totally made up genre string"]:
            assert isinstance(bucket_genre(value), str)
