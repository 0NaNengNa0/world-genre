-- Did this run load a plausible number of chart rows?
--
-- Returns a RATIO (today's rows over the trailing 7-day median), not a count.
-- An absolute floor would have to be retuned every time a country is added to
-- seeds/countries.csv or kworb changes a chart's length, and a threshold
-- nobody maintains is a threshold that gets widened until it means nothing.
--
-- Reads at most 8 partitions thanks to the snapshot_date predicate, so the
-- check costs a fraction of a cent even as the table grows.
WITH daily AS (
    SELECT snapshot_date, COUNT(*) AS rows_loaded
    FROM `{dataset}.chart_entries`
    WHERE snapshot_date > DATE_SUB(@snapshot_date, INTERVAL 8 DAY)
      AND snapshot_date <= @snapshot_date
    GROUP BY snapshot_date
),
observed AS (
    -- COALESCE, not a plain scalar subquery: when today loaded nothing at all
    -- there is no row to select, and a NULL here would propagate through
    -- SAFE_DIVIDE into a NULL value - which the gate treats as a failure, but
    -- for the wrong reason and with no numbers to show.
    SELECT COALESCE(
        (SELECT rows_loaded FROM daily WHERE snapshot_date = @snapshot_date), 0
    ) AS observed_rows
),
baseline AS (
    -- APPROX_QUANTILES(x, 2)[OFFSET(1)] is the median. Median rather than
    -- mean because one bad day in the window would drag a mean down and
    -- quietly lower the bar for the next one.
    SELECT APPROX_QUANTILES(rows_loaded, 2)[OFFSET(1)] AS median_rows
    FROM daily
    WHERE snapshot_date < @snapshot_date
)
SELECT
    observed.observed_rows,
    baseline.median_rows,
    -- With no history yet (first ever run, or a rebuilt table) there is
    -- nothing to compare against, so the check degrades to "did anything
    -- load at all" rather than passing vacuously.
    CASE
        WHEN baseline.median_rows IS NULL OR baseline.median_rows = 0
            THEN IF(observed.observed_rows > 0, 1.0, 0.0)
        ELSE SAFE_DIVIDE(observed.observed_rows, baseline.median_rows)
    END AS value
FROM observed
CROSS JOIN baseline;
