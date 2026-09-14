-- Resolve artist origin from the MusicBrainz mirror, in the warehouse.
--
-- This is the batch replacement for per-artist API lookups. `enrich_artists`
-- resolves ~50 artists a night at ~1 request/second against a backlog that was
-- growing; this resolves every artist the mirror can reach in one statement,
-- and leaves the API to handle only what is left.
--
-- THREE TIERS, and the third one is the point.
--
--   1. We already hold MusicBrainz's own id (from Last.fm's top-artists
--      response, persisted by run_load). Exact: no matching, no normalisation,
--      no possibility of collision.
--   2. Exactly one MusicBrainz artist answers to this name - either because
--      only one id carries it at all, or because several do but exactly one
--      claims it as its PRIMARY name rather than an alias.
--   3. Anything else resolves to NOTHING. Measured on 2026-09-14: of 790
--      matched names, 242 were shared primary names - distinct real artists
--      genuinely called the same thing. No rule breaks that tie from the name
--      alone, so the honest answer is to leave origin_country NULL and let
--      coverage_percentage report it, which the published payload already does.
--
-- The tempting fourth tier would be "pick the candidate whose country matches
-- where the artist charted". That is CIRCULAR: domestic share measures how much
-- of a country's listening is by artists from that country, so deriving origin
-- from chart country would manufacture the very number it feeds. The precedent
-- is in this repo already - the Deezer matcher guessed instead of declining and
-- produced ~29 percent bad matches.
--
-- resolved_at is set on success only. An artist the mirror cannot reach keeps
-- resolved_at NULL and therefore stays on the API stage's worklist, which is
-- the intended division of labour rather than an oversight.
MERGE `{dataset}.artists` T
USING (
  WITH unique_names AS (
    -- One row per name the mirror can resolve unambiguously. Names that map to
    -- several artists with no single primary claimant are absent by
    -- construction - that is tier 3, expressed as an omission rather than a
    -- branch.
    SELECT
      match_name,
      ARRAY_AGG(mbid ORDER BY is_primary DESC LIMIT 1)[OFFSET(0)] AS mbid
    FROM `{dataset}.mb_artist_names`
    GROUP BY match_name
    HAVING COUNT(DISTINCT mbid) = 1 OR COUNTIF(is_primary) = 1
  ),
  candidates AS (
    -- Tier 1: exact, by MusicBrainz id.
    SELECT
      a.artist_name,
      m.mbid,
      m.country,
      m.formed_year,
      'mb_dump_mbid' AS resolved_by
    FROM `{dataset}.artists` a
    JOIN `{dataset}.mb_artists` m ON m.mbid = a.mbid
    WHERE a.resolved_at IS NULL
      AND a.mbid IS NOT NULL

    UNION ALL

    -- Tier 2: by unambiguous name. Restricted to artists with no id, so the
    -- two tiers are disjoint and no artist can appear twice.
    SELECT
      a.artist_name,
      u.mbid,
      m.country,
      m.formed_year,
      'mb_dump_name' AS resolved_by
    FROM `{dataset}.artists` a
    JOIN unique_names u ON u.match_name = a.match_name
    JOIN `{dataset}.mb_artists` m ON m.mbid = u.mbid
    WHERE a.resolved_at IS NULL
      AND a.mbid IS NULL
      AND a.match_name IS NOT NULL
  )
  -- BigQuery rejects a MERGE whose source matches a target row more than once.
  -- The tiers are disjoint by their WHERE clauses so this can never actually
  -- choose between different values; it is here so that a future third tier
  -- cannot turn a logic error into a runtime failure at 5am.
  SELECT
    artist_name,
    ANY_VALUE(mbid) AS mbid,
    ANY_VALUE(country) AS country,
    ANY_VALUE(formed_year) AS formed_year,
    ANY_VALUE(resolved_by) AS resolved_by
  FROM candidates
  GROUP BY artist_name
) S
ON T.artist_name = S.artist_name
WHEN MATCHED THEN UPDATE SET
  -- COALESCE, not assignment: tier 1 matched BECAUSE the id was already there,
  -- and overwriting a verified id with the one we looked it up by is pointless
  -- at best.
  T.mbid = COALESCE(T.mbid, S.mbid),
  T.origin_country = S.country,
  T.formed_year = S.formed_year,
  T.resolved_at = CURRENT_TIMESTAMP(),
  T.resolved_by = S.resolved_by;
