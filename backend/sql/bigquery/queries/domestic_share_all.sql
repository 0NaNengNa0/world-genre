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
-- BigQuery has no aggregate FILTER clause, which Postgres supports. The
-- equivalents used below are COUNTIF(cond) and SUM(CASE WHEN cond THEN x END).
--
-- Not a like-for-like rewrite in one respect: SUM over an all-NULL set returns
-- NULL, exactly as `SUM(...) FILTER` did when nothing matched, so the COALESCE
-- wrappers stay load-bearing rather than decorative.
--
-- COUNT(DISTINCT x) FILTER has no COUNTIF form - COUNTIF counts rows, not
-- distinct values - so that one becomes COUNT(DISTINCT CASE WHEN ... END),
-- which skips NULLs and therefore counts only the matching values.
WITH latest AS (
    SELECT country_code, MAX(snapshot_date) AS snapshot_date
    FROM `{dataset}.chart_entries`
    GROUP BY country_code
),
entries AS (
    SELECT
        c.country_code,
        COALESCE(c.daily_streams, c.weekly_streams, c.total_streams, 0) AS streams,
        a.origin_country
    FROM `{dataset}.chart_entries` c
    JOIN latest l
      ON l.country_code = c.country_code
     AND l.snapshot_date = c.snapshot_date
    LEFT JOIN `{dataset}.artists` a ON a.artist_name = c.artist_name
)
SELECT
    country_code,
    COALESCE(SUM(streams), 0) AS total_streams,
    COALESCE(SUM(CASE WHEN origin_country IS NOT NULL THEN streams END), 0)
        AS classified_streams,
    -- origin_country compared against the grouping key: an artist counts as
    -- domestic when their origin matches the chart they're appearing on.
    COALESCE(SUM(CASE WHEN origin_country = country_code THEN streams END), 0)
        AS domestic_streams,
    COUNT(*) AS entry_count,
    COUNTIF(origin_country IS NOT NULL) AS classified_entries
FROM entries
GROUP BY country_code;
