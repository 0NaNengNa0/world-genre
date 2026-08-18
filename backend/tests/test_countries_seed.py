"""Tests for scripts/generate_countries_seed.py.

The point of these is the lastfm_name column specifically: Last.fm's
geo.getTopArtists matches on ISO 3166-1 names and fails *silently* (HTTP
200, empty artist list) when given anything else, so a wrong name here
costs a country all its data with no error anywhere. That's not
hypothetical - it's what happened with South Korea.
"""
import csv

import pytest

from scripts.generate_countries_seed import (
    KWORB_COUNTRY_CODES,
    LASTFM_NAME_OVERRIDES,
    OUTPUT_PATH,
    build_rows,
)

pytest.importorskip("pycountry")


class TestCommittedSeedMatchesGenerator:
    """The generator being correct is not the same as the file being correct.

    Every test below this class passed while the committed CSV still carried
    "Bolivia, Plurinational State of" and "Czechia" - the exact two values
    the overrides exist to prevent - because nothing ever compared the
    generator's output to the file on disk. Four countries silently collected
    zero artists for as long as that drift went unnoticed.
    """

    def _committed_rows(self) -> list[dict]:
        with OUTPUT_PATH.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_committed_csv_is_what_the_generator_produces(self):
        assert self._committed_rows() == build_rows(KWORB_COUNTRY_CODES), (
            "seeds/countries.csv is stale - rerun "
            "`python -m scripts.generate_countries_seed`"
        )

    def test_no_committed_lastfm_name_uses_a_formal_iso_qualifier(self):
        # Last.fm indexes countries by everyday name. The formal ISO variants
        # all contain a comma ("Bolivia, Plurinational State of") and return
        # an empty list rather than an error, so they can only be caught by
        # looking for the shape. Genuine exceptions are pinned as overrides.
        offenders = [
            row["lastfm_name"]
            for row in self._committed_rows()
            if "," in row["lastfm_name"]
            and row["kworb_code"] not in LASTFM_NAME_OVERRIDES
            and row["lastfm_name"] != "Korea, Republic of"
        ]
        assert offenders == []


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
