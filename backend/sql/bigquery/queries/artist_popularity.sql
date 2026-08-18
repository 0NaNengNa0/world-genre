-- One country's artists, measured by every source we have.
--
-- Parameters: @code, @limit
--
-- The three columns are NOT interchangeable and must never be summed:
--
--   streams   Spotify plays on this country's chart today (chart_entries)
--   listeners Last.fm users in this country who played the artist
--   fans      Deezer follows worldwide, not scoped to any country
--
-- They measure different populations - Spotify's userbase, Last.fm's
-- scrobbler community, Deezer's - so the interesting thing is where they
-- DISAGREE. An artist huge on Spotify here but invisible on Last.fm is a
-- real finding about which audience is listening, not a data error.
--
-- FULL OUTER JOIN rather than inner: an artist can chart without having
-- Last.fm listeners (new release, no scrobbles yet) or have listeners
-- without charting (catalogue favourite). Dropping either would quietly
-- bias the comparison toward artists that happen to appear in both.
--
-- Careful: psycopg2 scans this whole file for placeholders without stripping
-- SQL comments, so a bare percent sign - even in a comment - is read as a
-- malformed parameter. Spell the word out anywhere in this file.
WITH latest_chart AS (
    SELECT MAX(snapshot_date) AS snapshot_date
    FROM `{dataset}.chart_entries`
    WHERE country_code = @code
),
latest_listeners AS (
    SELECT MAX(snapshot_date) AS snapshot_date
    FROM `{dataset}.country_artist_listeners`
    WHERE country_code = @code
),
streams AS (
    SELECT
        c.artist_name,
        SUM(COALESCE(c.daily_streams, c.weekly_streams, c.total_streams, 0)) AS streams
    FROM `{dataset}.chart_entries` c
    JOIN latest_chart l ON l.snapshot_date = c.snapshot_date
    WHERE c.country_code = @code
    GROUP BY c.artist_name
),
listeners AS (
    SELECT cal.artist_name, cal.listeners
    FROM `{dataset}.country_artist_listeners` cal
    JOIN latest_listeners l ON l.snapshot_date = cal.snapshot_date
    WHERE cal.country_code = @code
)
SELECT
    COALESCE(s.artist_name, li.artist_name) AS artist_name,
    s.streams,
    li.listeners,
    a.deezer_fans
FROM streams s
FULL OUTER JOIN listeners li ON li.artist_name = s.artist_name
LEFT JOIN `{dataset}.artists` a ON a.artist_name = COALESCE(s.artist_name, li.artist_name)
-- Artists measured by BOTH sources come first. Ranking purely by streams
-- filled the table with chart-toppers that have no Last.fm presence at all
-- (only 6-34 percent of chart artists appear in Last.fm's top lists), so
-- every comparison column read as a dash - which defeats the point of a
-- table whose job is to show where the sources disagree. Within that,
-- streams then listeners.
ORDER BY
    (s.streams IS NOT NULL AND li.listeners IS NOT NULL) DESC,
    s.streams DESC NULLS LAST,
    li.listeners DESC NULLS LAST
LIMIT @limit;
