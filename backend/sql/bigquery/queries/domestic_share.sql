-- How much of a country's chart streaming goes to artists from that country,
-- versus imported music. Backs the "domestic share" figure on the country
-- detail view.
--
-- Parameters: @code
--
-- Weighted by streams, not by row count. Ten low-charting domestic tracks and
-- one global hit are not equal halves of a country's listening, and counting
-- rows would say they were.
--
-- Coverage is returned alongside the answer, deliberately. MusicBrainz does
-- not know an origin country for every charting artist, so the share is
-- computed over the streams where origin IS known - and a share of 40 percent
-- means something very different at 90 percent coverage than at 15. Reporting
-- the denominator is the difference between a statistic and a guess.
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
    SELECT MAX(snapshot_date) AS snapshot_date
    FROM `{dataset}.chart_entries`
    WHERE country_code = @code
),
entries AS (
    SELECT
        c.artist_name,
        -- Fall back through the stream columns: kworb leaves daily blank for
        -- some entries while still reporting weekly totals, and dropping
        -- those rows would quietly bias the result toward whatever kind of
        -- track reports daily figures.
        COALESCE(c.daily_streams, c.weekly_streams, c.total_streams, 0) AS streams,
        a.origin_country
    FROM `{dataset}.chart_entries` c
    JOIN latest l ON l.snapshot_date = c.snapshot_date
    LEFT JOIN `{dataset}.artists` a ON a.artist_name = c.artist_name
    WHERE c.country_code = @code
)
SELECT
    COALESCE(SUM(streams), 0) AS total_streams,
    COALESCE(SUM(CASE WHEN origin_country IS NOT NULL THEN streams END), 0)
        AS classified_streams,
    COALESCE(SUM(CASE WHEN origin_country = @code THEN streams END), 0)
        AS domestic_streams,
    COUNT(*) AS entry_count,
    COUNTIF(origin_country IS NOT NULL) AS classified_entries
FROM entries;
