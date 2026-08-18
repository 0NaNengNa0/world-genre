-- Which pairs of countries have the most similar genre taste, by counting
-- how many of their top genres (each country's latest snapshot) overlap.
-- Not wired to an API endpoint - kept here as a documented, directly
-- runnable example query (psql "$DATABASE_URL" -f sql/queries/similar_countries.sql)
-- since "most similar pair" isn't really a page anyone would look at
-- repeatedly the way a trending-genres endpoint is, it's more of an
-- analytical one-off - exactly the kind of thing a data engineer is
-- expected to be able to answer ad hoc with SQL, not necessarily productionize.
WITH latest_snapshot AS (
    SELECT country_code, MAX(snapshot_date) AS snapshot_date
    FROM `{dataset}.country_genre_scores`
    GROUP BY country_code
),
latest_genres AS (
    SELECT cgs.country_code, cgs.genre
    FROM `{dataset}.country_genre_scores` cgs
    JOIN latest_snapshot ls
      ON ls.country_code = cgs.country_code
     AND ls.snapshot_date = cgs.snapshot_date
)
SELECT
    a.country_code AS country_a,
    b.country_code AS country_b,
    COUNT(*) AS shared_genres,
    ARRAY_AGG(a.genre ORDER BY a.genre) AS genres_in_common
FROM latest_genres a
JOIN latest_genres b
  ON a.genre = b.genre
 AND a.country_code < b.country_code  -- self-join without mirrored (A,B)/(B,A) duplicate pairs
GROUP BY a.country_code, b.country_code
ORDER BY shared_genres DESC
LIMIT 20;
