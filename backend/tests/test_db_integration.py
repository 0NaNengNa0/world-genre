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


class TestChartEntriesAndDomesticShare:
    def _load_chart(self, cur, code, entries, snapshot=date(2026, 1, 1)):
        """Writes chart_entries + artists rows the way run_load does."""
        cur.execute(
            "INSERT INTO countries (code, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (code, code.upper()),
        )
        for i, (artist, streams) in enumerate(entries, start=1):
            cur.execute(
                """
                INSERT INTO chart_entries (country_code, snapshot_date, position,
                                           artist_name, track_name, daily_streams)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (code, snapshot, i, artist, f"track{i}", streams),
            )
            cur.execute(
                "INSERT INTO artists (artist_name) VALUES (%s) ON CONFLICT DO NOTHING",
                (artist,),
            )

    def test_domestic_share_is_weighted_by_streams_not_row_count(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            # Three domestic tracks with small numbers, one huge import: by
            # row count this is 75 percent domestic, by streams it's 10.
            self._load_chart(
                cur, "zz",
                [("Local A", 10), ("Local B", 10), ("Local C", 10), ("Global Star", 270)],
            )
            cur.execute(
                "UPDATE artists SET origin_country='zz', resolved_at=now() "
                "WHERE artist_name LIKE 'Local%'"
            )
            cur.execute(
                "UPDATE artists SET origin_country='us', resolved_at=now() "
                "WHERE artist_name = 'Global Star'"
            )

        from app.services.countries import get_country_detail

        share = get_country_detail("zz")["domestic_share"]
        assert share["domestic_percentage"] == pytest.approx(10.0)
        assert share["coverage_percentage"] == pytest.approx(100.0)

    def test_unresolved_artists_reduce_coverage_not_the_domestic_share(
        self, pg_database_url
    ):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            self._load_chart(cur, "zz", [("Known", 50), ("Unknown", 50)])
            cur.execute(
                "UPDATE artists SET origin_country='zz', resolved_at=now() "
                "WHERE artist_name='Known'"
            )

        from app.services.countries import get_country_detail

        share = get_country_detail("zz")["domestic_share"]
        # The one attributable artist is domestic, so 100 percent - but only
        # half the streams could be attributed. Reporting 50 percent domestic
        # here would be inventing a fact about the unresolved half.
        assert share["domestic_percentage"] == pytest.approx(100.0)
        assert share["coverage_percentage"] == pytest.approx(50.0)
        assert share["classified_entries"] == 1
        assert share["total_entries"] == 2

    def test_none_when_no_origins_resolved_yet(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            self._load_chart(cur, "zz", [("A", 10), ("B", 20)])

        from app.services.countries import get_country_detail

        # The artists dimension fills in over successive runs, so early on
        # the honest answer is "can't say", not "0 percent domestic".
        assert get_country_detail("zz")["domestic_share"] is None

    def test_falls_back_to_weekly_streams_when_daily_is_missing(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO countries (code, name) VALUES ('zz','Z') ON CONFLICT DO NOTHING"
            )
            # kworb leaves daily blank on some entries while still reporting
            # weekly; dropping those would bias the result.
            cur.execute(
                "INSERT INTO chart_entries (country_code, snapshot_date, position,"
                " artist_name, daily_streams, weekly_streams)"
                " VALUES ('zz', %s, 1, 'A', NULL, 700)",
                (date(2026, 1, 1),),
            )
            cur.execute(
                "INSERT INTO artists (artist_name, origin_country, resolved_at)"
                " VALUES ('A','zz',now())"
            )

        from app.services.countries import get_country_detail

        share = get_country_detail("zz")["domestic_share"]
        assert share["domestic_percentage"] == pytest.approx(100.0)
        assert share["coverage_percentage"] == pytest.approx(100.0)

    def test_reloading_the_same_day_upserts_chart_entries(self, pg_database_url):
        init_db()
        snapshot = date(2026, 1, 1)
        with get_connection() as conn, conn.cursor() as cur:
            self._load_chart(cur, "zz", [("A", 10)], snapshot)
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chart_entries (country_code, snapshot_date, position,
                                           artist_name, daily_streams)
                VALUES ('zz', %s, 1, 'A', 999)
                ON CONFLICT (country_code, snapshot_date, position)
                DO UPDATE SET daily_streams = EXCLUDED.daily_streams
                """,
                (snapshot,),
            )
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT daily_streams FROM chart_entries WHERE country_code='zz'")
            assert cur.fetchall() == [(999,)]


class TestTopTracksAndBatchedShare:
    def _chart(self, cur, code, entries, snapshot=date(2026, 1, 1)):
        cur.execute(
            "INSERT INTO countries (code, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (code, code.upper()),
        )
        for i, (artist, track, streams) in enumerate(entries, start=1):
            cur.execute(
                """
                INSERT INTO chart_entries (country_code, snapshot_date, position,
                                           artist_name, track_name, daily_streams)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (code, snapshot, i, artist, track, streams),
            )
            cur.execute(
                "INSERT INTO artists (artist_name) VALUES (%s) ON CONFLICT DO NOTHING",
                (artist,),
            )

    def test_top_tracks_ordered_by_chart_position(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            self._chart(
                cur, "zz",
                [("A", "First", 300), ("B", "Second", 200), ("C", "Third", 100)],
            )

        from app.services.countries import get_country_detail

        tracks = get_country_detail("zz")["top_tracks"]
        assert [t["track"] for t in tracks] == ["First", "Second", "Third"]
        assert tracks[0]["daily_streams"] == 300
        assert tracks[0]["artist"] == "A"

    def test_track_limit_is_respected(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            self._chart(cur, "zz", [(f"A{i}", f"T{i}", 10) for i in range(30)])

        from app.services.countries import get_country_detail

        assert len(get_country_detail("zz", track_limit=5)["top_tracks"]) == 5

    def test_summary_carries_domestic_share_for_every_country_in_one_query(
        self, pg_database_url
    ):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            self._chart(cur, "aa", [("Local", "x", 90), ("Foreign", "y", 10)])
            self._chart(cur, "bb", [("Foreign", "z", 100)])
            cur.execute(
                "UPDATE artists SET origin_country='aa', resolved_at=now() "
                "WHERE artist_name='Local'"
            )
            cur.execute(
                "UPDATE artists SET origin_country='us', resolved_at=now() "
                "WHERE artist_name='Foreign'"
            )

        from app.services.countries import get_country_summaries

        by_code = {s["code"]: s for s in get_country_summaries()}
        # Grouped query, not one per country - the map colours 76 shapes by
        # this and per-country would rebuild the N+1 the endpoint removed.
        assert by_code["aa"]["domestic_share"]["domestic_percentage"] == pytest.approx(90.0)
        # bb charts only a foreign artist, so 0 percent domestic is a real
        # measurement here, not missing data.
        assert by_code["bb"]["domestic_share"]["domestic_percentage"] == pytest.approx(0.0)

    def test_summary_share_is_none_for_countries_with_no_resolved_origins(
        self, pg_database_url
    ):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            self._chart(cur, "zz", [("Unknown", "t", 50)])

        from app.services.countries import get_country_summaries

        (summary,) = get_country_summaries()
        assert summary["domestic_share"] is None


class TestHiddenGemsAndGlobalArtists:
    def _chart(self, cur, code, entries, snapshot=date(2026, 1, 1)):
        cur.execute(
            "INSERT INTO countries (code, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (code, code.upper()),
        )
        for i, (artist, streams) in enumerate(entries, start=1):
            cur.execute(
                "INSERT INTO chart_entries (country_code, snapshot_date, position,"
                " artist_name, track_name, daily_streams) VALUES (%s,%s,%s,%s,%s,%s)",
                (code, snapshot, i, artist, f"t{i}", streams),
            )
            cur.execute(
                "INSERT INTO artists (artist_name) VALUES (%s) ON CONFLICT DO NOTHING",
                (artist,),
            )

    def test_global_superstar_cannot_be_a_hidden_gem(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            # Superstar charts everywhere with huge numbers; Local charts in
            # one country with far fewer. Each country loaded in one call -
            # positions restart at 1 per call and are part of the key.
            self._chart(cur, "aa", [("Superstar", 1_000_000), ("Local", 5_000)])
            self._chart(cur, "bb", [("Superstar", 1_000_000)])
            self._chart(cur, "cc", [("Superstar", 1_000_000)])

        from app.services.countries import get_country_detail

        gems = get_country_detail("aa")["hidden_gems"]
        names = [g["artist"] for g in gems]
        # Superstar charts in every country, so LN(3/3)=0 - excluded by
        # construction rather than by any blocklist, despite 200x the streams.
        assert "Superstar" not in names
        assert names == ["Local"]

    def test_gem_reach_is_reported_so_the_claim_is_checkable(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            self._chart(cur, "aa", [("Wide", 100), ("Narrow", 90)])
            self._chart(cur, "bb", [("Wide", 100)])
            self._chart(cur, "cc", [("Wide", 100)])

        from app.services.countries import get_country_detail

        (gem,) = [g for g in get_country_detail("aa")["hidden_gems"]]
        assert gem["artist"] == "Narrow"
        assert gem["country_count"] == 1
        assert gem["total_countries"] == 3

    def test_streams_are_summed_across_an_artists_chart_positions(self, pg_database_url):
        # An artist can hold several positions at once; ranking on one row
        # would under-count anyone with two hits.
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            self._chart(cur, "aa", [("Two Hits", 50), ("Two Hits", 60), ("One Hit", 100)])
            self._chart(cur, "bb", [("Filler", 10)])

        from app.services.countries import get_country_detail

        gems = {g["artist"]: g["streams"] for g in get_country_detail("aa")["hidden_gems"]}
        assert gems["Two Hits"] == 110

    def test_global_artists_rank_by_worldwide_streams(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            # Broad charts modestly in three countries; Narrow dominates one.
            self._chart(cur, "aa", [("Broad", 100), ("Narrow", 250)])
            self._chart(cur, "bb", [("Broad", 100)])
            self._chart(cur, "cc", [("Broad", 100)])

        from app.services.countries import get_global_artists

        by_artist = {a["artist"]: a for a in get_global_artists()}
        assert by_artist["Broad"]["streams"] == 300
        assert by_artist["Broad"]["country_count"] == 3
        assert by_artist["Narrow"]["country_count"] == 1

    def test_delta_is_none_until_a_second_snapshot_exists(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            self._chart(cur, "aa", [("A", 100)])

        from app.services.countries import get_global_artists

        # None, not 0 - "unchanged" and "nothing to compare" differ.
        assert get_global_artists()[0]["delta"] is None

    def test_delta_compares_against_the_previous_snapshot(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            self._chart(cur, "aa", [("A", 100)], date(2026, 1, 1))
            self._chart(cur, "aa", [("A", 175)], date(2026, 1, 2))

        from app.services.countries import get_global_artists

        artist = get_global_artists()[0]
        assert artist["streams"] == 175
        assert artist["delta"] == 75


class TestGenreDetail:
    def _setup(self, cur):
        cur.execute(
            "INSERT INTO countries (code, name) VALUES ('zz','Testland') "
            "ON CONFLICT DO NOTHING"
        )
        snapshot = date(2026, 1, 1)
        # Two artists tagged j-pop, one of them charting far higher.
        for artist, streams in [("Big Act", 900), ("Small Act", 100)]:
            cur.execute(
                "INSERT INTO chart_entries (country_code, snapshot_date, position,"
                " artist_name, daily_streams) VALUES ('zz',%s,%s,%s,%s)",
                (snapshot, 1 if artist == "Big Act" else 2, artist, streams),
            )
        for artist in ["Big Act", "Small Act", "Not Charting"]:
            cur.execute(
                "INSERT INTO country_artist_genres (country_code, genre, artist_name,"
                " snapshot_date) VALUES ('zz','j-pop',%s,%s)",
                (artist, snapshot),
            )
        cur.execute(
            "INSERT INTO genres (genre, summary, url, resolved_at)"
            " VALUES ('j-pop','Japanese popular music.','http://x',now())"
        )

    def test_artists_ranked_by_streams(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            self._setup(cur)

        from app.services.countries import get_genre_detail

        detail = get_genre_detail("zz", "j-pop")
        assert [a["artist"] for a in detail["artists"]] == [
            "Big Act",
            "Small Act",
            "Not Charting",
        ]
        assert detail["artists"][0]["streams"] == 900

    def test_non_charting_artists_are_kept_with_null_streams(self, pg_database_url):
        # The genre link comes from tags, which cover artists who aren't on
        # today's chart. They're still valid examples of the genre - an INNER
        # JOIN would silently drop them.
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            self._setup(cur)

        from app.services.countries import get_genre_detail

        last = get_genre_detail("zz", "j-pop")["artists"][-1]
        assert last["artist"] == "Not Charting"
        assert last["streams"] is None

    def test_includes_the_description(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            self._setup(cur)

        from app.services.countries import get_genre_detail

        detail = get_genre_detail("zz", "j-pop")
        assert detail["summary"] == "Japanese popular music."
        assert detail["url"] == "http://x"

    def test_genre_without_a_description_still_returns_artists(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            self._setup(cur)
            cur.execute(
                "INSERT INTO country_artist_genres (country_code, genre, artist_name,"
                " snapshot_date) VALUES ('zz','rock','Big Act',%s)",
                (date(2026, 1, 1),),
            )

        from app.services.countries import get_genre_detail

        detail = get_genre_detail("zz", "rock")
        assert detail["summary"] is None
        assert [a["artist"] for a in detail["artists"]] == ["Big Act"]

    def test_unknown_genre_returns_an_empty_list_not_an_error(self, pg_database_url):
        init_db()
        with get_connection() as conn, conn.cursor() as cur:
            self._setup(cur)

        from app.services.countries import get_genre_detail

        # A 404 would imply the country is wrong; an empty list correctly says
        # this genre simply has nothing linked here.
        detail = get_genre_detail("zz", "polka")
        assert detail is not None
        assert detail["artists"] == []

    def test_unknown_country_returns_none(self, pg_database_url):
        init_db()
        from app.services.countries import get_genre_detail

        assert get_genre_detail("qq", "j-pop") is None


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
