"""Integration tests for the warehouse layer (sql/schema.sql,
scripts/run_load.py, app/services/countries.py, the /api/genres/trending
query) against a real Postgres - see tests/conftest.py's pg_database_url
fixture for how that Postgres instance is provisioned without Docker.

Unlike test_cleansing.py/test_genre_buckets.py, these necessarily touch
the database, so they're slower and live in their own file - keeps
`pytest -k "not db"` a fast option for iterating on pure logic.
"""
from datetime import date

import pytest

from app.core.db import get_connection
from scripts.run_init_db import main as init_db
from scripts.run_load import load_country


def _sample_record(pop_score=50, rock_score=30, extra_genres=None):
    """run_cleanse writes ONE `genres` list per country carrying both scores
    for every genre - not separate pre-truncated popularity/distinctiveness
    lists. Percentages have to divide by the real total, which only works if
    the full distribution is stored."""
    return {
        "artist_count": 20,
        "top_artists": ["Artist A", "Artist B", "Artist C"],
        "genres": [
            {"genre": "pop", "score": pop_score, "sources": ["lastfm"],
             "distinctiveness": 0.0},
            {"genre": "rock", "score": rock_score, "sources": ["musicbrainz"],
             "distinctiveness": 0.0},
            *(extra_genres or []),
        ],
    }


class TestSchemaAndLoad:
    def test_init_db_creates_tables(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )
            tables = {row[0] for row in cur.fetchall()}
        assert {
            "countries",
            "country_snapshots",
            "country_genre_scores",
            "country_top_artists",
        } <= tables

    def test_load_country_writes_all_tables(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            load_country(cur, "zz", "Testland", _sample_record(), date(2026, 1, 1))

        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT name FROM countries WHERE code = 'zz'")
            assert cur.fetchone() == ("Testland",)

            cur.execute(
                "SELECT artist_count FROM country_snapshots WHERE country_code = 'zz'"
            )
            assert cur.fetchone() == (20,)

            cur.execute(
                "SELECT genre, score, sources FROM country_genre_scores "
                "WHERE country_code = 'zz' ORDER BY score DESC"
            )
            rows = cur.fetchall()
            assert rows[0] == ("pop", 50, ["lastfm"])
            assert rows[1] == ("rock", 30, ["musicbrainz"])

            cur.execute(
                "SELECT artist_name, rank FROM country_top_artists "
                "WHERE country_code = 'zz' ORDER BY rank"
            )
            assert cur.fetchall() == [
                ("Artist A", 1), ("Artist B", 2), ("Artist C", 3)
            ]

    def test_reloading_same_day_upserts_not_duplicates(self, pg_database_url):
        init_db()
        snapshot = date(2026, 1, 1)
        with get_connection() as conn, conn.cursor() as cur:
            load_country(cur, "zz", "Testland", _sample_record(pop_score=50), snapshot)
        with get_connection() as conn, conn.cursor() as cur:
            load_country(cur, "zz", "Testland", _sample_record(pop_score=99), snapshot)

        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT score FROM country_genre_scores "
                "WHERE country_code = 'zz' AND genre = 'pop' AND snapshot_date = %s",
                (snapshot,),
            )
            rows = cur.fetchall()
            # One row, holding the second load's value - not two rows.
            assert rows == [(99,)]


class TestDistinctiveness:
    def test_stores_every_genre_with_both_scores(self, pg_database_url):
        init_db()
        record = _sample_record(
            pop_score=50,
            extra_genres=[
                {"genre": "bollywood", "score": 20, "sources": ["lastfm"],
                 "distinctiveness": 38.9},
            ],
        )
        with get_connection() as conn, conn.cursor() as cur:
            load_country(cur, "zz", "Testland", record, date(2026, 1, 1))

        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT genre, score, distinctiveness FROM country_genre_scores "
                "WHERE country_code = 'zz' ORDER BY genre"
            )
            rows = cur.fetchall()
        assert rows == [
            ("bollywood", 20, pytest.approx(38.9, rel=1e-3)),
            ("pop", 50, 0.0),
            ("rock", 30, 0.0),
        ]

    def test_api_excludes_zero_distinctiveness_genres(self, pg_database_url):
        init_db()
        record = _sample_record(
            extra_genres=[
                {"genre": "bollywood", "score": 20, "sources": ["lastfm"],
                 "distinctiveness": 38.9},
            ],
        )
        with get_connection() as conn, conn.cursor() as cur:
            load_country(cur, "zz", "Testland", record, date(2026, 1, 1))

        from app.services.countries import get_country_summaries

        (summary,) = get_country_summaries()
        # pop/rock are universal (distinctiveness 0) so they distinguish
        # nothing and must not appear in this list.
        assert summary["distinctive_genres"] == ["bollywood"]
        assert "pop" in summary["top_genres"]

    def test_summary_caps_each_list_at_five(self, pg_database_url):
        # The table now holds the FULL distribution, so an unbounded query
        # would return far more than the 5 the summary card promises.
        init_db()
        record = {
            "artist_count": 20,
            "top_artists": ["A"],
            "genres": [
                {"genre": g, "score": 90 - i, "sources": ["lastfm"],
                 "distinctiveness": 30.0 - i}
                for i, g in enumerate(
                    ["pop", "rock", "jazz", "blues", "metal",
                     "bollywood", "qawwali", "gamelan", "soca", "kwaito"]
                )
            ],
        }
        with get_connection() as conn, conn.cursor() as cur:
            load_country(cur, "zz", "Testland", record, date(2026, 1, 1))

        from app.services.countries import get_country_summaries

        (summary,) = get_country_summaries()
        assert len(summary["top_genres"]) == 5
        assert len(summary["distinctive_genres"]) == 5


class TestCountryDetail:
    def test_percentages_sum_to_one_hundred_with_other_slice(self, pg_database_url):
        init_db()
        # 10 genres scoring 10 each, but only 3 shown: the "other" slice has
        # to account for the remaining 7 or the chart lies.
        record = {
            "artist_count": 20,
            "top_artists": ["A", "B"],
            "genres": [
                {"genre": f"g{i}", "score": 10, "sources": ["lastfm"],
                 "distinctiveness": 0.0}
                for i in range(10)
            ],
        }
        with get_connection() as conn, conn.cursor() as cur:
            load_country(cur, "zz", "Testland", record, date(2026, 1, 1))

        from app.services.countries import get_country_detail

        pop = get_country_detail("zz", genre_limit=3)["popularity"]
        assert len(pop["genres"]) == 3
        # Each of 10 equal genres is 10 percent; 3 shown, 7 in "other".
        assert all(g["percentage"] == pytest.approx(10.0) for g in pop["genres"])
        assert pop["other_percentage"] == pytest.approx(70.0)
        assert pop["other_genre_count"] == 7
        listed = sum(g["percentage"] for g in pop["genres"])
        assert listed + pop["other_percentage"] == pytest.approx(100.0)

    def test_percentage_divides_by_true_total_not_truncated_one(self, pg_database_url):
        init_db()
        record = {
            "artist_count": 5,
            "top_artists": ["A"],
            "genres": [
                {"genre": "big", "score": 75, "sources": ["lastfm"], "distinctiveness": 0.0},
                {"genre": "small", "score": 25, "sources": ["lastfm"], "distinctiveness": 0.0},
            ],
        }
        with get_connection() as conn, conn.cursor() as cur:
            load_country(cur, "zz", "Testland", record, date(2026, 1, 1))

        from app.services.countries import get_country_detail

        # Only one genre returned, but its share must still be 75 - not 100,
        # which is what dividing by the returned rows' total would give.
        pop = get_country_detail("zz", genre_limit=1)["popularity"]
        assert pop["genres"][0]["percentage"] == pytest.approx(75.0)
        assert pop["other_percentage"] == pytest.approx(25.0)

    def test_distinctiveness_mode_ranks_and_weights_independently(self, pg_database_url):
        init_db()
        record = {
            "artist_count": 5,
            "top_artists": ["A"],
            "genres": [
                # Hugely popular but universal - 0 distinctiveness.
                {"genre": "pop", "score": 900, "sources": ["lastfm"],
                 "distinctiveness": 0.0},
                # Barely played but unique to this country.
                {"genre": "bollywood", "score": 30, "sources": ["lastfm"],
                 "distinctiveness": 75.0},
                {"genre": "qawwali", "score": 10, "sources": ["lastfm"],
                 "distinctiveness": 25.0},
            ],
        }
        with get_connection() as conn, conn.cursor() as cur:
            load_country(cur, "zz", "Testland", record, date(2026, 1, 1))

        from app.services.countries import get_country_detail

        detail = get_country_detail("zz")
        # Popularity is led by pop; distinctiveness excludes it entirely.
        assert detail["popularity"]["genres"][0]["genre"] == "pop"
        distinct = detail["distinctiveness"]["genres"]
        assert [g["genre"] for g in distinct] == ["bollywood", "qawwali"]
        # Shares are of total DISTINCTIVENESS (75+25), not of play score.
        assert distinct[0]["percentage"] == pytest.approx(75.0)
        assert distinct[1]["percentage"] == pytest.approx(25.0)
        assert detail["distinctiveness"]["other_percentage"] == pytest.approx(0.0)

    def test_zero_weight_genres_excluded_from_distinctiveness_denominator(
        self, pg_database_url
    ):
        # The bug this guards: counting universal genres in the denominator
        # would shrink every real slice and leave a huge phantom "other".
        init_db()
        record = {
            "artist_count": 5,
            "top_artists": ["A"],
            "genres": [
                {"genre": "pop", "score": 900, "sources": [], "distinctiveness": 0.0},
                {"genre": "rock", "score": 800, "sources": [], "distinctiveness": 0.0},
                {"genre": "bollywood", "score": 10, "sources": [], "distinctiveness": 40.0},
            ],
        }
        with get_connection() as conn, conn.cursor() as cur:
            load_country(cur, "zz", "Testland", record, date(2026, 1, 1))

        from app.services.countries import get_country_detail

        distinct = get_country_detail("zz")["distinctiveness"]
        assert len(distinct["genres"]) == 1
        assert distinct["genres"][0]["percentage"] == pytest.approx(100.0)
        assert distinct["other_percentage"] == pytest.approx(0.0)
        assert distinct["other_genre_count"] == 0

    def test_artist_limit_and_ordering(self, pg_database_url):
        init_db()
        record = {
            "artist_count": 100,
            "top_artists": [f"Artist {i:03d}" for i in range(100)],
            "genres": [],
        }
        with get_connection() as conn, conn.cursor() as cur:
            load_country(cur, "zz", "Testland", record, date(2026, 1, 1))

        from app.services.countries import get_country_detail

        detail = get_country_detail("zz")
        assert len(detail["artists"]) == 100
        assert detail["artists"][0] == "Artist 000"  # rank order preserved
        assert get_country_detail("zz", artist_limit=10)["artists"] == [
            f"Artist {i:03d}" for i in range(10)
        ]

    def test_unknown_country_returns_none(self, pg_database_url):
        init_db()
        from app.services.countries import get_country_detail

        assert get_country_detail("zz") is None

    def test_country_with_no_genres_does_not_divide_by_zero(self, pg_database_url):
        init_db()
        record = {"artist_count": 0, "top_artists": [], "genres": []}
        with get_connection() as conn, conn.cursor() as cur:
            load_country(cur, "zz", "Testland", record, date(2026, 1, 1))

        from app.services.countries import get_country_detail

        detail = get_country_detail("zz")
        for breakdown in (detail["popularity"], detail["distinctiveness"]):
            assert breakdown["genres"] == []
            # 0, not 100 - there is no remainder to describe, and 100 would
            # render as a full "other (0 genres)" ring instead of the empty
            # state.
            assert breakdown["other_percentage"] == pytest.approx(0.0)
            assert breakdown["other_genre_count"] == 0


class TestGetCountrySummaries:
    def test_returns_latest_snapshot_per_country(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            load_country(cur, "zz", "Testland", _sample_record(pop_score=50), date(2026, 1, 1))
        with get_connection() as conn, conn.cursor() as cur:
            load_country(cur, "zz", "Testland", _sample_record(pop_score=70), date(2026, 1, 2))

        from app.services.countries import get_country_summaries

        summaries = get_country_summaries()
        assert len(summaries) == 1
        zz = summaries[0]
        assert zz["code"] == "zz"
        assert zz["name"] == "Testland"
        assert zz["top_genres"][0] == "pop"  # highest score first
        assert zz["artist_count"] == 20


class TestTrendingGenres:
    def test_delta_between_two_snapshots(self, pg_database_url):
        init_db()
        record_day1 = _sample_record(pop_score=50, rock_score=30)
        record_day2 = _sample_record(pop_score=65, rock_score=20)
        with get_connection() as conn, conn.cursor() as cur:
            load_country(cur, "zz", "Testland", record_day1, date(2026, 1, 1))
        with get_connection() as conn, conn.cursor() as cur:
            load_country(cur, "zz", "Testland", record_day2, date(2026, 1, 2))

        from app.api.routes.genres import get_trending_genres

        result = get_trending_genres()
        by_genre = {g.genre: g for g in result.genres}
        assert by_genre["pop"].delta == 15
        assert by_genre["rock"].delta == -10

    def test_single_snapshot_returns_no_trends(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            load_country(cur, "zz", "Testland", _sample_record(), date(2026, 1, 1))

        from app.api.routes.genres import get_trending_genres

        result = get_trending_genres()
        assert result.genres == []
