-- What share of raw genre tags did cleansing fail to classify?
--
-- The signal: Last.fm's crowd tags and MusicBrainz's genres are free text, and
-- cleansing.normalize_genre plus genre_buckets.bucket_genre map them onto a
-- curated taxonomy. Tags that normalize to nothing, or fall through to the
-- "other" bucket, are counted as unclassified and then DROPPED before scoring.
-- That drop is why this check reads cleanse_quality rather than
-- country_genre_scores: the unclassified tags are, by construction, absent
-- from every other table in the warehouse.
--
-- What a rise means: the tag vocabulary has moved underneath the bucket list.
-- Not a crash, not a missing row - a slow decay in the quality of every genre
-- donut on the site, invisible to every other check here.
--
-- WEIGHTED by tag volume, not the mean of per-country rates. Averaging rates
-- would give a country contributing twelve tags the same influence as one
-- contributing several thousand, so a single small market with a bad run
-- could swing the global figure or, worse, hide one.
WITH day AS (
    SELECT total_genre_tags, unclassified_genre_tags
    FROM `{dataset}.cleanse_quality`
    WHERE snapshot_date = @snapshot_date
)
SELECT
    -- Zero tags means run_cleanse produced no report for this partition, so
    -- there is nothing to judge and the check abstains rather than reporting
    -- a flattering zero.
    COALESCE(SUM(total_genre_tags), 0) AS sample_size,
    COALESCE(SUM(unclassified_genre_tags), 0) AS unclassified_tags,
    SAFE_DIVIDE(SUM(unclassified_genre_tags), SUM(total_genre_tags)) AS value
FROM day;
