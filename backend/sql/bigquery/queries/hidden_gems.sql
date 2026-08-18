-- Artists who chart strongly in ONE country but barely anywhere else.
--
-- Parameters: @code, @limit
--
-- Same idea as the genre distinctiveness scoring, applied to artists instead:
-- weight what a country actually streams by how rare that artist is across
-- the rest of the world.
--
--     gem_score = streams_here * LN(total_countries / countries_charting_them)
--
-- An artist charting everywhere scores LN(1) = 0 and drops out entirely, so
-- the global superstars that dominate every chart can't appear here by
-- construction - no blocklist needed. An artist charting in one country out
-- of 76 gets the largest multiplier.
--
-- Streams are summed per artist first, because an artist can hold several
-- chart positions at once and each is a separate fact row; ranking on a
-- single row would under-count anyone with two hits.
--
-- Careful: psycopg2 scans this whole file for placeholders without stripping
-- SQL comments, so a bare percent sign - even in a comment - is read as a
-- malformed parameter. Spell the word out anywhere in this file.
WITH latest AS (
    SELECT country_code, MAX(snapshot_date) AS snapshot_date
    FROM `{dataset}.chart_entries`
    GROUP BY country_code
),
current_streams AS (
    SELECT
        c.country_code,
        c.artist_name,
        SUM(COALESCE(c.daily_streams, c.weekly_streams, c.total_streams, 0)) AS streams,
        MIN(c.position) AS best_position
    FROM `{dataset}.chart_entries` c
    JOIN latest l
      ON l.country_code = c.country_code
     AND l.snapshot_date = c.snapshot_date
    GROUP BY c.country_code, c.artist_name
),
reach AS (
    SELECT artist_name, COUNT(DISTINCT country_code) AS country_count
    FROM current_streams
    GROUP BY artist_name
),
scope AS (
    SELECT COUNT(DISTINCT country_code) AS total_countries FROM current_streams
)
SELECT
    cs.artist_name,
    cs.streams,
    cs.best_position,
    r.country_count,
    s.total_countries,
    -- Both Postgres casts are dropped, and only one of them was cosmetic.
    --
    -- The inner `to numeric` cast was load-bearing: in Postgres, dividing two
    -- integers truncates, so total_countries / country_count would have gone
    -- to 1 for every artist charting in more than half the countries, and to
    -- 0 above that - making LN(1) = 0 and silently zeroing their score.
    -- BigQuery's `/` always returns FLOAT64 regardless of operand types, so
    -- the division is already exact and the cast has nothing left to fix.
    --
    -- The outer cast existed only because Postgres has no
    -- round(double precision, integer). BigQuery's ROUND takes FLOAT64.
    ROUND(cs.streams * LN(s.total_countries / r.country_count), 1) AS gem_score
FROM current_streams cs
JOIN reach r ON r.artist_name = cs.artist_name
CROSS JOIN scope s
WHERE cs.country_code = @code
  -- Excludes artists charting in every country: their score is zero by
  -- definition, and returning them as "hidden" would be nonsense.
  AND r.country_count < s.total_countries
ORDER BY gem_score DESC
LIMIT @limit;
