-- What fraction of the artists we fetched tags for came out with a genre?
--
-- THE DENOMINATOR IS THE WHOLE POINT, and the first version of this check got
-- it wrong. Genre signal is built in cleansing.merge_genre_signals from
-- Last.fm tags and MusicBrainz genres, and the Last.fm extractor samples each
-- country's top ~100 artists by listeners. That is a different population
-- from the Spotify chart - schema.sql says so explicitly above
-- country_artist_listeners: "Last.fm covers artists that aren't charting at
-- all."
--
-- Dividing by charting artists therefore measures the OVERLAP of two
-- deliberately different populations, which read 0.157 and blocked a publish
-- over a regression that did not exist. Measured against the population the
-- genre pipeline is actually responsible for, the same day reads 0.948.
--
-- What this catches: a Last.fm API key expiring, a normalize_genre regression,
-- or the bucket taxonomy failing to cover what a country listens to - any of
-- which leave country pages rendering, just thinner.
WITH tagged AS (
    SELECT DISTINCT artist_name
    FROM `{dataset}.country_artist_listeners`
    WHERE snapshot_date = @snapshot_date
),
classified AS (
    SELECT DISTINCT artist_name
    FROM `{dataset}.country_artist_genres`
    WHERE snapshot_date = @snapshot_date
)
SELECT
    -- Zero artists fetched means the Last.fm stage did not run. That is not
    -- this check's failure to report - it abstains, and chart_volume speaks.
    COUNT(*) AS sample_size,
    COUNTIF(g.artist_name IS NOT NULL) AS classified_artists,
    IF(
        COUNT(*) = 0,
        0.0,
        SAFE_DIVIDE(COUNTIF(g.artist_name IS NOT NULL), COUNT(*))
    ) AS value
FROM tagged t
LEFT JOIN classified g ON g.artist_name = t.artist_name;
