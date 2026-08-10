"""Tests for scripts/generate_countries_seed.py.

The point of these is the lastfm_name column specifically: Last.fm's
geo.getTopArtists matches on ISO 3166-1 names and fails *silently* (HTTP
200, empty artist list) when given anything else, so a wrong name here
costs a country all its data with no error anywhere. That's not
hypothetical - it's what happened with South Korea.
"""
import pytest

from scripts.generate_countries_seed import (
    KWORB_COUNTRY_CODES,
    LASTFM_NAME_OVERRIDES,
    build_rows,
)

pytest.importorskip("pycountry")


class TestBuildRows:
    def test_all_kworb_codes_resolve(self):
        rows = build_rows(KWORB_COUNTRY_CODES)
        assert len(rows) == len(set(KWORB_COUNTRY_CODES))

    def test_unknown_code_is_skipped_not_fatal(self):
        rows = build_rows(["us", "zzz"])
        assert [r["kworb_code"] for r in rows] == ["us"]

    def test_south_korea_uses_iso_name_for_api_and_common_name_for_display(self):
        # The original bug: "South Korea" returns nothing from Last.fm.
        (kr,) = build_rows(["kr"])
        assert kr["lastfm_name"] == "Korea, Republic of"
        assert kr["country_name"] == "South Korea"

    def test_turkey_override_wins_over_current_iso_name(self):
        # ISO renamed Turkey -> Türkiye in 2022; Last.fm still matches the
        # old name, so the override must not be silently reverted by a
        # pycountry upgrade.
        (tr,) = build_rows(["tr"])
        assert tr["lastfm_name"] == "Turkey"

    def test_countries_observed_returning_zero_artists_are_overridden(self):
        # Each of these returned 0 artists from Last.fm on the first
        # 76-country run using its formal ISO name, which also poisoned their
        # artist names via the kworb fallback. Pinning them so a pycountry
        # upgrade or a seed regeneration can't silently reintroduce it.
        expected = {
            "bo": "Bolivia",
            "cz": "Czech Republic",
            "tw": "Taiwan",
            "ve": "Venezuela",
        }
        rows = {r["kworb_code"]: r for r in build_rows(list(expected))}
        for code, name in expected.items():
            assert rows[code]["lastfm_name"] == name

    def test_every_override_targets_a_real_kworb_country(self):
        # Guards against an override lingering for a country later dropped
        # from the chart source, where it would sit unused and misleading.
        assert set(LASTFM_NAME_OVERRIDES) <= set(KWORB_COUNTRY_CODES)

    def test_rows_are_sorted_and_deduped(self):
        rows = build_rows(["us", "ca", "us"])
        assert [r["kworb_code"] for r in rows] == ["ca", "us"]

    def test_all_three_columns_always_populated(self):
        for row in build_rows(KWORB_COUNTRY_CODES):
            assert row["lastfm_name"] and row["kworb_code"] and row["country_name"]
