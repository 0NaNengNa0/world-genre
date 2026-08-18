-- Top genres for one country's latest snapshot, with each genre's share of
-- that country's total weight. Backs the donut chart in the country detail
-- view, for both of its modes.
--
-- Parameters: @code, @limit
-- Substituted in Python (NOT bind parameters): {dataset}, {weight_column}
--
-- {weight_column} is substituted because a bind parameter can carry a value
-- but never an identifier. It is restricted to a hardcoded allowlist at the
-- call site (_WEIGHT_COLUMNS in app/services/warehouse.py) and must never
-- take caller-supplied text.
--
-- Two modes share this query because the arithmetic is identical and only the
-- weighted column differs:
--   score           - share of what the country actually plays
--   distinctiveness - share of what sets the country apart from the rest
--
-- The share is computed over EVERY qualifying genre, not just the returned
-- top N, or each slice is inflated by whatever the tail contributed. Hence
-- SUM(...) OVER () inside the CTE, before the LIMIT, with the total returned
-- on each row so the caller can derive the leftover "other" slice and have
-- the chart add up honestly.
--
-- Rows where the weight is 0 are excluded rather than charted: in
-- distinctiveness mode those are the genres common to every country, which
-- contribute nothing to what makes this one different, and including them
-- would inflate the denominator and shrink every real slice.
--
-- Ported from Postgres. Two changes:
--
-- 1. The cast before ROUND is gone. It existed because Postgres has no
--    round(double precision, integer), only round(numeric, integer), so a
--    FLOAT8 distinctiveness had to be cast first. BigQuery's ROUND accepts
--    FLOAT64 directly.
--
-- 2. The correlated MAX(snapshot_date) subquery is unchanged in meaning but
--    now prunes partitions, since snapshot_date is the partitioning column.
--    That makes it a cost reduction as well as a filter - BigQuery bills on
--    bytes scanned, and without it this reads every day ever loaded.
WITH latest AS (
    SELECT genre, score, distinctiveness, sources
    FROM `{dataset}.country_genre_scores`
    WHERE country_code = @code
      AND snapshot_date = (
          SELECT MAX(snapshot_date)
          FROM `{dataset}.country_genre_scores`
          WHERE country_code = @code
      )
      AND {weight_column} > 0
),
with_total AS (
    SELECT
        genre,
        score,
        distinctiveness,
        sources,
        SUM({weight_column}) OVER () AS total_weight,
        COUNT(*) OVER () AS genre_count
    FROM latest
)
SELECT
    genre,
    score,
    distinctiveness,
    sources,
    total_weight,
    genre_count,
    -- NULLIF guards a country whose weights are all 0, which would otherwise
    -- divide by zero rather than returning an empty chart.
    ROUND(100.0 * {weight_column} / NULLIF(total_weight, 0), 2) AS percentage
FROM with_total
ORDER BY {weight_column} DESC
LIMIT @limit;
