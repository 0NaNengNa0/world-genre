-- Did every country that charted this week still chart today?
--
-- This check exists because chart_volume demonstrably cannot do its job.
-- With 75 countries and ~7,310 rows, one country dropping out costs about
-- 1.3 percent of the total - well inside the day-to-day noise of a row-count
-- ratio, which ran 0.9997 to 1.001 across the same four days. A country could
-- disappear from the scrape permanently and every other check would stay
-- green forever. Counting countries makes it unmissable: 74 where there were
-- 75 is a whole number, not a rounding error.
--
-- The expected set is "countries seen in the trailing week", not
-- seeds/countries.csv. That matters: Andorra is in the seed list and has
-- never appeared in the chart data, so comparing against the seed list would
-- fail every single run, and a gate that fails every run gets switched off.
-- Comparing against recent behaviour means the check calibrates itself to
-- whatever the roster actually is, and a country legitimately retired from
-- the source stops being expected once it ages out of the window.
WITH recent AS (
    SELECT DISTINCT country_code
    FROM `{dataset}.chart_entries`
    WHERE snapshot_date > DATE_SUB(@snapshot_date, INTERVAL 8 DAY)
      AND snapshot_date < @snapshot_date
),
present AS (
    SELECT DISTINCT country_code
    FROM `{dataset}.chart_entries`
    WHERE snapshot_date = @snapshot_date
),
missing AS (
    SELECT r.country_code
    FROM recent r
    LEFT JOIN present p ON p.country_code = r.country_code
    WHERE p.country_code IS NULL
)
SELECT
    -- LEAST of the two sides, so this abstains when EITHER is empty: no
    -- history means nothing is expected and the check would pass vacuously,
    -- while an empty partition would make it report all 75 countries missing
    -- and bury chart_volume's one accurate failure under a second alarm for
    -- the same cause. One root cause should produce one verdict.
    LEAST(
        (SELECT COUNT(*) FROM recent), (SELECT COUNT(*) FROM present)
    ) AS sample_size,
    (SELECT COUNT(*) FROM present) AS countries_present,
    -- The codes themselves, so dq_runs answers "which one" without a rerun
    -- against a partition that has since been overwritten.
    (SELECT STRING_AGG(country_code, ',' ORDER BY country_code) FROM missing) AS missing_codes,
    CAST((SELECT COUNT(*) FROM missing) AS FLOAT64) AS value;
