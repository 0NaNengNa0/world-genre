-- Genre score deltas between each country's two most recent load dates.
-- Wired up at GET /api/genres/trending (app/api/routes/genres.py), published
-- to genres-trending.json. Kept in its own file rather than as a Python
-- string so it is directly runnable:
--
--     bq query --use_legacy_sql=false --dataset_id=PROJECT:world_genre < this
--
-- Needs at least two distinct snapshot_dates per country to return anything,
-- so with one day of history it legitimately returns zero rows.
--
-- SYMMETRIC BY CONSTRUCTION, and it was not always. This query used to end
-- `ORDER BY delta DESC LIMIT 50`, which takes the fifty largest RISES and
-- nothing else - fallers sort to the bottom and were cut off on every single
-- run. The Trends view had therefore never shown a falling genre in its life.
-- Nothing errored, no test failed, and the page looked populated, which is why
-- it went unnoticed: a LIMIT over a signed quantity silently picks a side.
--
-- Taking the top N of each direction keeps the payload the same size and the
-- same shape, so the serving layer needed no change.
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
),
deltas AS (
    SELECT
        country_code,
        genre,
        score,
        previous_score,
        (score - previous_score) AS delta
    FROM scored_with_previous
    -- Excludes genres that are brand-new this run: they have no previous
    -- score, so "how much did it move" has no answer. Note the mirror case is
    -- NOT handled here - see the comment at the bottom.
    WHERE previous_score IS NOT NULL
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (ORDER BY delta DESC) AS rising_rank,
        ROW_NUMBER() OVER (ORDER BY delta ASC) AS falling_rank
    FROM deltas
)
-- KNOWN ASYMMETRY, left deliberately rather than overlooked.
--
-- A genre that vanished from a country entirely has no row in the latest
-- snapshot, so LAG never produces a pair for it and it cannot appear here -
-- even though disappearing is the largest fall available. Rises have the same
-- blind spot at the other end, handled above by excluding brand-new genres so
-- at least the two sides are treated alike.
--
-- Fixing it properly means abandoning LAG for a FULL OUTER JOIN between the
-- two snapshots, treating a missing side as zero. That is a bigger change than
-- it looks: "score dropped to zero" and "we stopped measuring this genre" are
-- different events and this table cannot distinguish them, so the honest
-- version needs to decide which one it is reporting before it reports it.

SELECT
    country_code,
    genre,
    score,
    previous_score,
    delta
FROM ranked
WHERE rising_rank <= 25 OR falling_rank <= 25
ORDER BY delta DESC;
