-- Top genres for one country's latest snapshot, with each genre's share of
-- that country's total weight. Backs the donut chart in the country detail
-- view (GET /api/countries/{code}), for both of its modes.
--
-- Parameters: %(code)s, %(limit)s
-- Formatted (NOT a bind parameter): {weight_column}
--
-- {weight_column} is substituted in Python because SQL bind parameters can't
-- carry an identifier - only a value. It is therefore restricted to a
-- hardcoded allowlist at the call site (see _WEIGHT_COLUMNS in
-- app/services/countries.py); it must never take caller-supplied text.
--
-- Two modes share this query because the arithmetic is identical and only
-- the column being weighted differs:
--   score           - share of what the country actually plays
--   distinctiveness - share of what sets the country apart from the rest
--
-- The share has to be computed over EVERY qualifying genre, not just the
-- returned top N, or each slice is inflated by whatever the tail contributed.
-- That's why SUM(...) OVER () runs inside the CTE - before the LIMIT - and
-- why the total comes back on each row, so the caller can derive the leftover
-- "other" slice and have the chart add up honestly.
--
-- Rows where the weight is 0 are excluded rather than charted: in
-- distinctiveness mode those are the genres common to every country, which
-- contribute nothing to what makes this one different, and including them
-- would inflate the denominator and shrink every real slice.
--
-- Careful: psycopg2 scans this whole file for placeholders without stripping
-- SQL comments first, so a bare percent sign - even inside a comment - is
-- read as a malformed parameter and raises "argument formats can't be mixed".
-- Spell the word out rather than using the symbol anywhere in this file.
WITH latest AS (
    SELECT genre, score, distinctiveness, sources
    FROM country_genre_scores
    WHERE country_code = %(code)s
      AND snapshot_date = (
          SELECT MAX(snapshot_date)
          FROM country_genre_scores
          WHERE country_code = %(code)s
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
    --
    -- The numeric cast is required, not stylistic: distinctiveness is REAL,
    -- so this expression comes out as double precision, and Postgres has no
    -- round(double precision, integer) - only round(numeric, integer). The
    -- score column happens to work without it because integers promote to
    -- numeric, which is exactly why this only broke once the second mode
    -- started using the same query.
    ROUND((100.0 * {weight_column} / NULLIF(total_weight, 0))::numeric, 2) AS percentage
FROM with_total
ORDER BY {weight_column} DESC
LIMIT %(limit)s;
