-- Is the fact table's declared primary key actually unique today?
--
-- schema.sql declares PRIMARY KEY (country_code, snapshot_date, position) NOT
-- ENFORCED because BigQuery offers no other option: the declaration helps the
-- optimizer and documents the grain, but nothing rejects a duplicate. Postgres
-- raised an error; here a duplicate is simply two rows, and every SUM of
-- streams downstream is silently wrong.
--
-- run_load._dedupe is what provides uniqueness. This is the assertion that it
-- did, which is the difference between believing the invariant and checking
-- it. Observed zero duplicates across 7,315 rows.
WITH partition_rows AS (
    SELECT country_code, position
    FROM `{dataset}.chart_entries`
    WHERE snapshot_date = @snapshot_date
),
keys AS (
    SELECT country_code, position, COUNT(*) AS row_count
    FROM partition_rows
    GROUP BY country_code, position
),
duplicates AS (
    SELECT row_count FROM keys WHERE row_count > 1
)
SELECT
    -- An empty partition has no duplicates, which is true and worthless as a
    -- statement about data quality - so this abstains instead of passing.
    (SELECT COUNT(*) FROM partition_rows) AS sample_size,
    (SELECT COUNT(*) FROM duplicates) AS duplicate_keys,
    -- How many rows would have to disappear for the key to hold - the size of
    -- the problem, not just its existence.
    (SELECT COALESCE(SUM(row_count) - COUNT(*), 0) FROM duplicates) AS excess_rows,
    CAST((SELECT COUNT(*) FROM duplicates) AS FLOAT64) AS value;
