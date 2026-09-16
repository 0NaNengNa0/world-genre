-- Which genres are gaining or losing ground in each country, week over week.
-- Wired up at GET /api/genres/trending, published to genres-trending.json.
--
-- WHAT THIS MEASURES, and why it changed on 2026-09-16.
--
-- The first version compared each country's two most recent snapshots and
-- ranked by the change in raw score. Measured against real data it produced
-- this, across every country and every genre:
--
--     delta distribution:  {-1: 1,  0: 24,  +1: 25}
--
-- Nothing moved. Not because the charts are static - 82-87% of (country,
-- position) slots change track day over day - but because those swaps happen
-- mostly WITHIN the same genres. A country's top 200 turns over substantially
-- while its genre mix stays nearly identical, so a day-over-day delta on a
-- coarse integer count was measuring rounding.
--
-- Two changes, each fixing a different defect:
--
-- 1. A SEVEN-DAY WINDOW instead of yesterday. The signal is real at this scale
--    and invisible at one day. "Trending this week" is also a more honest
--    claim than "trending since yesterday" for a chart that updates daily.
--
-- 2. SHARE, not raw count. Ranking by absolute change made `ie rock 220->219`
--    rank alongside a small genre doubling, because a big genre in a big
--    market moves more points for the same underlying nothing. Share - each
--    genre as a percentage of its country's total - makes a small market and
--    a large one comparable, which is the only way a cross-country ranking
--    means anything. `delta` is therefore in PERCENTAGE POINTS.
--
-- A third defect is fixed by the FULL OUTER JOIN below: a genre that vanished
-- from a country had no row in the latest snapshot, so the old LAG() never
-- paired it and it could not appear - even though disappearing is the largest
-- fall available. Absence is now read as zero share, which is what it means
-- here: country_genre_scores carries a row only for genres actually present.
WITH dates AS (
    SELECT country_code, snapshot_date
    FROM `{dataset}.country_genre_scores`
    GROUP BY country_code, snapshot_date
),
window_candidates AS (
    -- Per country, not globally: countries do not all load on the same days,
    -- and a country that missed a run should be compared against its own
    -- history rather than against a date it has no data for.
    --
    -- l.latest_date is in the GROUP BY rather than aggregated. It is
    -- functionally dependent on country_code, but BigQuery does not infer
    -- that - every non-aggregated column must be grouped.
    SELECT
        d.country_code,
        l.latest_date,
        COALESCE(
            -- The most recent snapshot at least six days older than the
            -- latest. Six, not seven, so a run that slips by a few hours
            -- still finds last week's rather than falling through.
            MAX(
                CASE
                    WHEN d.snapshot_date
                         <= DATE_SUB(l.latest_date, INTERVAL 6 DAY)
                    THEN d.snapshot_date
                END
            ),
            -- Less than a week of history: compare against the oldest
            -- snapshot there is, so the view works from day two rather than
            -- staying empty for a week.
            MIN(CASE WHEN d.snapshot_date < l.latest_date THEN d.snapshot_date END)
        ) AS baseline_date
    FROM dates d
    JOIN (
        SELECT country_code, MAX(snapshot_date) AS latest_date
        FROM dates
        GROUP BY country_code
    ) l ON l.country_code = d.country_code
    GROUP BY d.country_code, l.latest_date
),
window_ends AS (
    -- A country with exactly one snapshot has no baseline, and dropping it
    -- here is load-bearing. Left in, its prior side would be empty, the FULL
    -- OUTER JOIN below would read every genre as appearing from nothing, and
    -- a brand-new country would flood the rising list on its first day. A
    -- country with no comparison has no trend - that is a skip, not a rise.
    --
    -- Filtered in its own CTE rather than with HAVING: the predicate would
    -- reference an aliased aggregate expression, and BigQuery resolves names
    -- in HAVING against the SELECT list first. That is exactly how
    -- merges/resolve_artists.sql failed with "Aggregations of aggregations
    -- are not allowed".
    SELECT * FROM window_candidates WHERE baseline_date IS NOT NULL
),
shares AS (
    -- Each genre as a percentage of its country's total score on that day.
    --
    -- Note this cannot prune partitions: the dates come from another table, so
    -- BigQuery scans the whole (small) table rather than two partitions. At a
    -- few thousand rows per day that is cheaper than the machinery to avoid
    -- it, but it would not be at a hundred times the size.
    SELECT
        cgs.country_code,
        cgs.snapshot_date,
        cgs.genre,
        cgs.score,
        SAFE_DIVIDE(
            cgs.score,
            SUM(cgs.score) OVER (
                PARTITION BY cgs.country_code, cgs.snapshot_date
            )
        ) * 100 AS share
    FROM `{dataset}.country_genre_scores` cgs
    JOIN window_ends w ON w.country_code = cgs.country_code
    WHERE cgs.snapshot_date IN (w.latest_date, w.baseline_date)
),
-- NOT named `current` and `prior`. CURRENT is a RESERVED KEYWORD in BigQuery
-- (it opens CURRENT_DATE, CURRENT_TIMESTAMP, and the CURRENT ROW window
-- frame), so a CTE called `current` fails with "Expected keyword SELECT but
-- got keyword CURRENT". sqlglot parses it without complaint - reserved-word
-- collisions are a dialect fact, not a syntax one, which is why the offline
-- parser cannot catch them and a BigQuery dry run can.
latest_shares AS (
    SELECT s.*
    FROM shares s
    JOIN window_ends w
      ON w.country_code = s.country_code AND w.latest_date = s.snapshot_date
),
baseline_shares AS (
    SELECT s.*
    FROM shares s
    JOIN window_ends w
      ON w.country_code = s.country_code AND w.baseline_date = s.snapshot_date
),
paired AS (
    SELECT
        COALESCE(c.country_code, p.country_code) AS country_code,
        COALESCE(c.genre, p.genre) AS genre,
        COALESCE(c.score, 0) AS score,
        COALESCE(p.score, 0) AS previous_score,
        COALESCE(c.share, 0) AS share,
        COALESCE(p.share, 0) AS previous_share,
        COALESCE(c.share, 0) - COALESCE(p.share, 0) AS delta
    FROM latest_shares c
    FULL OUTER JOIN baseline_shares p
      ON p.country_code = c.country_code AND p.genre = c.genre
),
ranked AS (
    SELECT
        *,
        -- Two rankings rather than one ORDER BY: `delta` is a SIGNED
        -- quantity, and a single LIMIT over a signed quantity silently picks
        -- a side. The previous version ended `ORDER BY delta DESC LIMIT 50`
        -- and had therefore never once shown a falling genre.
        ROW_NUMBER() OVER (ORDER BY delta DESC) AS rising_rank,
        ROW_NUMBER() OVER (ORDER BY delta ASC) AS falling_rank
    FROM paired
)
SELECT
    r.country_code,
    r.genre,
    r.score,
    r.previous_score,
    ROUND(r.share, 2) AS share,
    ROUND(r.previous_share, 2) AS previous_share,
    ROUND(r.delta, 2) AS delta,
    FORMAT_DATE('%Y-%m-%d', w.latest_date) AS snapshot_date,
    FORMAT_DATE('%Y-%m-%d', w.baseline_date) AS previous_date
FROM ranked r
JOIN window_ends w ON w.country_code = r.country_code
WHERE r.rising_rank <= 25 OR r.falling_rank <= 25
ORDER BY r.delta DESC;
