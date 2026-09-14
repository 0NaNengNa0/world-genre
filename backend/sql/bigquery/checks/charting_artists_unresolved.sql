-- What share of today's charting artists has nobody managed to look up?
--
-- This is the check that would have caught the thing nothing was watching.
-- Artist origin is filled in incrementally - the API stage resolves a bounded
-- number per run because MusicBrainz is ~1 request/second - and the story that
-- justifies that design is "it converges". On 2026-09-14 it turned out not to:
-- the backlog was 1,591 on the 13th and 1,602 after two full runs on the 14th,
-- because new charting artists arrive at roughly the rate the stage clears
-- them. That drift had been running for weeks with nothing measuring it.
--
-- Scoped to TODAY'S charting artists rather than the whole dimension, for two
-- reasons. It matches the grain of every other check here, so one partition is
-- one verdict. And it measures the thing the site actually shows: domestic
-- share is computed over artists on today's charts, so their coverage is what
-- determines whether the number on the page is trustworthy.
--
-- A LEFT JOIN, deliberately: an artist on today's chart with no row at all in
-- the dimension is as unresolved as one with a NULL resolved_at, and counting
-- only the rows that exist would hide exactly the artists that arrived today.
WITH todays_artists AS (
    SELECT DISTINCT artist_name
    FROM `{dataset}.chart_entries`
    WHERE snapshot_date = @snapshot_date
)
SELECT
    -- No charting artists means no partition to judge; chart_volume owns that
    -- failure and this abstains rather than reporting a flattering zero.
    COUNT(*) AS sample_size,
    COUNTIF(a.resolved_at IS NULL) AS unresolved,
    IF(
        COUNT(*) = 0,
        0.0,
        SAFE_DIVIDE(COUNTIF(a.resolved_at IS NULL), COUNT(*))
    ) AS value
FROM todays_artists t
LEFT JOIN `{dataset}.artists` a ON a.artist_name = t.artist_name;
