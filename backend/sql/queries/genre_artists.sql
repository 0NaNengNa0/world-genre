-- Example artists behind one genre in one country, biggest first.
--
-- Parameters: %(code)s, %(genre)s, %(limit)s
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
-- Careful: psycopg2 scans this whole file for placeholders without stripping
-- SQL comments, so a bare percent sign - even in a comment - is read as a
-- malformed parameter. Spell the word out anywhere in this file.
WITH latest_link AS (
    SELECT MAX(snapshot_date) AS snapshot_date
    FROM country_artist_genres
    WHERE country_code = %(code)s
),
latest_chart AS (
    SELECT MAX(snapshot_date) AS snapshot_date
    FROM chart_entries
    WHERE country_code = %(code)s
),
streams AS (
    SELECT
        c.artist_name,
        SUM(COALESCE(c.daily_streams, c.weekly_streams, c.total_streams, 0)) AS streams,
        MIN(c.position) AS best_position
    FROM chart_entries c
    JOIN latest_chart lc ON lc.snapshot_date = c.snapshot_date
    WHERE c.country_code = %(code)s
    GROUP BY c.artist_name
)
SELECT
    cag.artist_name,
    s.streams,
    s.best_position
FROM country_artist_genres cag
JOIN latest_link ll ON ll.snapshot_date = cag.snapshot_date
LEFT JOIN streams s ON s.artist_name = cag.artist_name
WHERE cag.country_code = %(code)s
  AND cag.genre = %(genre)s
ORDER BY s.streams DESC NULLS LAST, cag.artist_name
LIMIT %(limit)s;
