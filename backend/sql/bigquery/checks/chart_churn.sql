-- Did the charts actually move since the last snapshot?
--
-- This is the staleness check, and it exists because nothing else can be one.
-- run_load stamps every row with the date the pipeline ran, not the date the
-- source published, so a cached or frozen upstream page produces a run that is
-- complete, correctly shaped, right on volume - and identical to yesterday.
-- Comparing content against the previous snapshot is the only evidence
-- available that the data is new.
--
-- Compared per (country, position) slot rather than per country, so a handful
-- of quiet markets cannot mask a source-wide freeze. Observed 0.820-0.873
-- across four consecutive day-pairs.
WITH previous AS (
    SELECT MAX(snapshot_date) AS snapshot_date
    FROM `{dataset}.chart_entries`
    WHERE snapshot_date < @snapshot_date
),
today AS (
    SELECT country_code, position, track_name, artist_name
    FROM `{dataset}.chart_entries`
    WHERE snapshot_date = @snapshot_date
),
prior AS (
    SELECT c.country_code, c.position, c.track_name, c.artist_name
    FROM `{dataset}.chart_entries` c
    JOIN previous pv ON pv.snapshot_date = c.snapshot_date
),
compared AS (
    SELECT
        COUNT(*) AS compared_rows,
        -- COALESCE before comparing: NULL != NULL evaluates to NULL, which
        -- COUNTIF counts as false, so a slot that gained or lost a track name
        -- would register as unchanged. That is the opposite of the truth.
        COUNTIF(
            COALESCE(t.track_name, '') != COALESCE(p.track_name, '')
            OR COALESCE(t.artist_name, '') != COALESCE(p.artist_name, '')
        ) AS changed_rows
    FROM today t
    JOIN prior p
      ON p.country_code = t.country_code
     AND p.position = t.position
)
SELECT
    -- Nothing to compare against - the first run ever, or an empty partition -
    -- means this check abstains rather than passing on zero evidence. Absence
    -- of history is not evidence of freshness.
    compared_rows AS sample_size,
    changed_rows,
    SAFE_DIVIDE(changed_rows, compared_rows) AS value
FROM compared;
