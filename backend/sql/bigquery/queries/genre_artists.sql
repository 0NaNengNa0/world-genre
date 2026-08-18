-- Example artists behind EVERY requested genre in one country, biggest first.
--
-- Parameters: @code, @genres (ARRAY<STRING>), @limit
--
-- country_artist_genres says WHICH artists gave a genre its score;
-- chart_entries says how much each of them is actually streamed. Joining the
-- two ranks the examples by real listening rather than alphabetically, so the
-- names shown are ones a reader might recognise.
--
-- LEFT JOIN, not INNER: the genre link comes from Last.fm and MusicBrainz
-- tags, which cover artists who aren't currently charting. Those are still
-- legitimate examples of the genre in that country - they just sort last,
-- with null streams, instead of vanishing.
--
-- This used to take a single @genre and be called once per genre. At ~16
-- genres per country across 76 countries that was ~1,200 separate BigQuery
-- jobs for the publish step - each with the same fixed per-job overhead, and
-- each rescanning the same two tables. It now takes an array and ranks within
-- each genre using QUALIFY, so one job answers the whole country.
--
-- QUALIFY is what makes the per-genre LIMIT possible: a plain LIMIT would cap
-- the combined result, handing back 12 rows for the first genre and none for
-- the rest. ROW_NUMBER partitioned by genre applies the cap per group.
WITH latest_link AS (
    SELECT MAX(snapshot_date) AS snapshot_date
    FROM `{dataset}.country_artist_genres`
    WHERE country_code = @code
),
latest_chart AS (
    SELECT MAX(snapshot_date) AS snapshot_date
    FROM `{dataset}.chart_entries`
    WHERE country_code = @code
),
streams AS (
    SELECT
        c.artist_name,
        SUM(COALESCE(c.daily_streams, c.weekly_streams, c.total_streams, 0)) AS streams,
        MIN(c.position) AS best_position
    FROM `{dataset}.chart_entries` c
    JOIN latest_chart lc ON lc.snapshot_date = c.snapshot_date
    WHERE c.country_code = @code
    GROUP BY c.artist_name
)
SELECT
    cag.genre,
    cag.artist_name,
    s.streams,
    s.best_position
FROM `{dataset}.country_artist_genres` cag
JOIN latest_link ll ON ll.snapshot_date = cag.snapshot_date
LEFT JOIN streams s ON s.artist_name = cag.artist_name
WHERE cag.country_code = @code
  AND cag.genre IN UNNEST(@genres)
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY cag.genre
    -- NULLS LAST is explicit because BigQuery sorts NULLs first on DESC by
    -- default, which would put the non-charting artists at the top of every
    -- panel - the opposite of the intended ranking.
    ORDER BY s.streams DESC NULLS LAST, cag.artist_name
) <= @limit
ORDER BY cag.genre, s.streams DESC NULLS LAST, cag.artist_name;
