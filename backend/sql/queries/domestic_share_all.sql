-- Domestic streaming share for EVERY country in one query.
--
-- No parameters.
--
-- The per-country version (domestic_share.sql) is right for the detail view,
-- but the grid endpoint feeds 76 map shapes at once - running that query per
-- country would be exactly the N+1 pattern the summary endpoint was rewritten
-- to remove. Same arithmetic, grouped instead of filtered.
--
-- Coverage comes back per row for the same reason it does there: a domestic
-- share is uninterpretable without knowing how much of the country's
-- streaming could be attributed to a known artist origin at all.
--
-- Careful: psycopg2 scans this whole file for placeholders without stripping
-- SQL comments, so a bare percent sign - even in a comment - is read as a
-- malformed parameter. Spell the word out anywhere in this file.
WITH latest AS (
    SELECT country_code, MAX(snapshot_date) AS snapshot_date
    FROM chart_entries
    GROUP BY country_code
),
entries AS (
    SELECT
        c.country_code,
        COALESCE(c.daily_streams, c.weekly_streams, c.total_streams, 0) AS streams,
        a.origin_country
    FROM chart_entries c
    JOIN latest l
      ON l.country_code = c.country_code
     AND l.snapshot_date = c.snapshot_date
    LEFT JOIN artists a ON a.artist_name = c.artist_name
)
SELECT
    country_code,
    COALESCE(SUM(streams), 0) AS total_streams,
    COALESCE(SUM(streams) FILTER (WHERE origin_country IS NOT NULL), 0)
        AS classified_streams,
    -- origin_country compared against the grouping key: an artist counts as
    -- domestic when their origin matches the chart they're appearing on.
    COALESCE(SUM(streams) FILTER (WHERE origin_country = country_code), 0)
        AS domestic_streams,
    COUNT(*) AS entry_count,
    COUNT(*) FILTER (WHERE origin_country IS NOT NULL) AS classified_entries
FROM entries
GROUP BY country_code;
