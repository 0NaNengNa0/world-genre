-- Genre score deltas between each country's two most recent load dates.
-- Wired up at GET /api/genres/trending (app/api/routes/genres.py) - kept
-- in its own file rather than as a Python string so it's directly
-- runnable on its own: psql "$DATABASE_URL" -f sql/queries/trending_genres.sql
--
-- Needs at least two distinct snapshot_dates per country to return
-- anything (i.e. the pipeline needs to have run on two different days) -
-- with only one day of history so far this legitimately returns zero
-- rows, not a bug.
WITH latest_two_dates AS (
    -- DENSE_RANK, not ROW_NUMBER: if every country loaded on the same two
    -- calendar dates (the normal case), this still ranks per-country
    -- correctly even though the underlying dates are shared across rows.
    SELECT DISTINCT
        country_code,
        snapshot_date,
        DENSE_RANK() OVER (
            PARTITION BY country_code ORDER BY snapshot_date DESC
        ) AS recency
    FROM `{dataset}.country_genre_scores`
),
scored_with_previous AS (
    SELECT
        cgs.country_code,
        cgs.genre,
        cgs.score,
        cgs.snapshot_date,
        LAG(cgs.score) OVER (
            PARTITION BY cgs.country_code, cgs.genre ORDER BY cgs.snapshot_date
        ) AS previous_score
    FROM `{dataset}.country_genre_scores` cgs
    JOIN latest_two_dates ltd
      ON ltd.country_code = cgs.country_code
     AND ltd.snapshot_date = cgs.snapshot_date
     AND ltd.recency <= 2
)
SELECT
    country_code,
    genre,
    score,
    previous_score,
    (score - previous_score) AS delta
FROM scored_with_previous
WHERE previous_score IS NOT NULL  -- excludes genres that are brand-new this run
ORDER BY delta DESC
LIMIT 50;
