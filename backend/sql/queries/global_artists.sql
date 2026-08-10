-- Biggest artists worldwide by charted streaming, with how far they've
-- travelled and how they moved since the previous run.
--
-- Parameters: %(limit)s
--
-- "Global" here means summed across every country's chart, so an artist
-- charting modestly in forty countries can outrank one dominating a single
-- large market - which is the interesting comparison.
--
-- DENSE_RANK over snapshot dates per country, rather than MAX: countries
-- aren't guaranteed to have loaded on the same days, so a single global
-- "latest date" would silently drop any country that ran a day late.
--
-- `delta` is NULL, not 0, until a second snapshot exists for that artist.
-- Zero would claim they held flat, which is a different statement from
-- having nothing to compare against.
--
-- Careful: psycopg2 scans this whole file for placeholders without stripping
-- SQL comments, so a bare percent sign - even in a comment - is read as a
-- malformed parameter. Spell the word out anywhere in this file.
WITH ranked_dates AS (
    SELECT DISTINCT
        country_code,
        snapshot_date,
        DENSE_RANK() OVER (
            PARTITION BY country_code ORDER BY snapshot_date DESC
        ) AS recency
    FROM chart_entries
),
per_country AS (
    SELECT
        c.artist_name,
        c.country_code,
        rd.recency,
        SUM(COALESCE(c.daily_streams, c.weekly_streams, c.total_streams, 0)) AS streams
    FROM chart_entries c
    JOIN ranked_dates rd
      ON rd.country_code = c.country_code
     AND rd.snapshot_date = c.snapshot_date
    WHERE rd.recency <= 2
    GROUP BY c.artist_name, c.country_code, rd.recency
),
totals AS (
    SELECT
        artist_name,
        SUM(streams) FILTER (WHERE recency = 1) AS streams,
        SUM(streams) FILTER (WHERE recency = 2) AS previous_streams,
        COUNT(DISTINCT country_code) FILTER (WHERE recency = 1) AS country_count
    FROM per_country
    GROUP BY artist_name
)
SELECT
    t.artist_name,
    COALESCE(t.streams, 0) AS streams,
    t.previous_streams,
    CASE
        WHEN t.previous_streams IS NULL THEN NULL
        ELSE COALESCE(t.streams, 0) - t.previous_streams
    END AS delta,
    t.country_count,
    a.origin_country
FROM totals t
LEFT JOIN artists a ON a.artist_name = t.artist_name
WHERE t.streams IS NOT NULL
ORDER BY t.streams DESC
LIMIT %(limit)s;
