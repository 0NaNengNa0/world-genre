-- BigQuery warehouse schema for World Genre.
--
-- The Postgres original is sql/schema.sql; this is a port, not a rewrite, and
-- the table grain is identical. Four things had to change, and each has a
-- consequence worth knowing rather than a like-for-like replacement:
--
-- 1. TYPES. TEXT to STRING, INTEGER/BIGINT to INT64, REAL to FLOAT64,
--    TIMESTAMPTZ to TIMESTAMP (BigQuery timestamps are always UTC, so the
--    "with time zone" distinction disappears), and Postgres's TEXT[] to
--    ARRAY<STRING>.
--
-- 2. KEYS ARE NOT ENFORCED. BigQuery accepts PRIMARY KEY and FOREIGN KEY
--    declarations but never checks them - they exist to let the optimizer
--    eliminate joins. They are kept here because they still document the
--    grain, but the guarantee is gone: nothing stops a duplicate row.
--    scripts/run_load.py has to provide uniqueness itself, by deleting a
--    partition before appending to it rather than relying on ON CONFLICT.
--
-- 3. NO INDEXES. Partitioning and clustering replace them. Partitioning on
--    snapshot_date is what makes "the latest day for this country" cheap:
--    BigQuery bills on bytes scanned, so pruning to one partition is a cost
--    reduction, not just a speed one. Clustering on country_code sorts within
--    each partition, which is the other half of every access path here.
--
-- 4. DATASET IS SUBSTITUTED AT APPLY TIME. `{dataset}` is replaced by
--    scripts/run_init_bq.py. Deliberately plain string substitution rather
--    than str.format, matching the convention in sql/queries - a format field
--    collides with any brace that appears in a comment.
--
-- Percent signs are avoided throughout, as in every other .sql file here: the
-- Postgres driver scans whole query strings for placeholders and a literal
-- percent in prose breaks execution. BigQuery doesn't care, but keeping one
-- rule for all SQL is simpler than remembering which files are exempt.

CREATE TABLE IF NOT EXISTS `{dataset}.countries` (
    code STRING NOT NULL,
    name STRING NOT NULL,
    PRIMARY KEY (code) NOT ENFORCED
)
CLUSTER BY code;

-- One row per country per day: the total distinct artists resolved that run.
-- Kept separate from country_top_artists (which only stores the top N)
-- because "how many artists did we see" and "which N are worth showing" are
-- different questions.
CREATE TABLE IF NOT EXISTS `{dataset}.country_snapshots` (
    country_code STRING NOT NULL,
    snapshot_date DATE NOT NULL,
    artist_count INT64 NOT NULL,
    PRIMARY KEY (country_code, snapshot_date) NOT ENFORCED,
    FOREIGN KEY (country_code) REFERENCES `{dataset}.countries` (code) NOT ENFORCED
)
PARTITION BY snapshot_date
CLUSTER BY country_code;

-- One row per (country, genre, day) - the output of
-- cleansing.merge_genre_signals.
--
-- Two scores per row, not one, because they answer different questions:
-- `score` is raw popularity, `distinctiveness` is popularity weighted by
-- inverse document frequency across countries (see
-- cleansing.score_distinctiveness). Popularity alone is near-identical
-- everywhere - pop and rock chart in essentially every country - so ranking
-- by it makes every country look the same. Storing both means either ranking
-- is a plain ORDER BY rather than a recompute.
CREATE TABLE IF NOT EXISTS `{dataset}.country_genre_scores` (
    country_code STRING NOT NULL,
    genre STRING NOT NULL,
    score INT64 NOT NULL,
    distinctiveness FLOAT64 NOT NULL,
    -- ARRAY<STRING> rather than a join table: genre reconciliation only ever
    -- has 1-2 sources (Last.fm/MusicBrainz), so normalizing is pure overhead.
    -- Note BigQuery arrays cannot contain NULL elements, which is fine here -
    -- a source is always a non-empty name - but it is a real difference from
    -- Postgres arrays.
    sources ARRAY<STRING>,
    snapshot_date DATE NOT NULL,
    PRIMARY KEY (country_code, genre, snapshot_date) NOT ENFORCED,
    FOREIGN KEY (country_code) REFERENCES `{dataset}.countries` (code) NOT ENFORCED
)
PARTITION BY snapshot_date
CLUSTER BY country_code, genre;

CREATE TABLE IF NOT EXISTS `{dataset}.country_top_artists` (
    country_code STRING NOT NULL,
    artist_name STRING NOT NULL,
    rank INT64 NOT NULL,
    snapshot_date DATE NOT NULL,
    PRIMARY KEY (country_code, snapshot_date, rank) NOT ENFORCED,
    FOREIGN KEY (country_code) REFERENCES `{dataset}.countries` (code) NOT ENFORCED
)
PARTITION BY snapshot_date
CLUSTER BY country_code;

-- Artist dimension. Origin and formation year come from MusicBrainz and
-- Wikidata; both are nullable because coverage is genuinely partial.
--
-- `resolved_at` distinguishes "not looked up yet" from "looked up, and the
-- source genuinely has no country for them". Without it every rerun would
-- retry the same permanent misses forever.
--
-- Not partitioned: this is a slowly-changing dimension with no date grain,
-- and at a few thousand rows it is far below the ~1GB where partitioning
-- starts to pay for itself.
CREATE TABLE IF NOT EXISTS `{dataset}.artists` (
    artist_name STRING NOT NULL,
    mbid STRING,
    origin_country STRING,   -- ISO 3166-1 alpha-2, matching countries.code
    formed_year INT64,
    -- Deezer's global fan count. A different population from Spotify streams
    -- or Last.fm listeners, so it sits beside them rather than being folded
    -- into a single number.
    deezer_fans INT64,
    resolved_at TIMESTAMP,
    PRIMARY KEY (artist_name) NOT ENFORCED
)
CLUSTER BY artist_name;

-- Last.fm listener counts per artist per country.
--
-- A SECOND per-country popularity measure alongside chart_entries' Spotify
-- streams, measuring a genuinely different population: BTS's largest Last.fm
-- audience is Brazil, not the US, which the Spotify chart doesn't say.
-- Deliberately a separate table rather than a column on chart_entries,
-- because Last.fm covers artists that aren't charting at all.
CREATE TABLE IF NOT EXISTS `{dataset}.country_artist_listeners` (
    country_code STRING NOT NULL,
    artist_name STRING NOT NULL,
    listeners INT64,
    snapshot_date DATE NOT NULL,
    PRIMARY KEY (country_code, artist_name, snapshot_date) NOT ENFORCED,
    FOREIGN KEY (country_code) REFERENCES `{dataset}.countries` (code) NOT ENFORCED
)
PARTITION BY snapshot_date
CLUSTER BY country_code, artist_name;

-- Which artists caused a genre to score in a country. A bridge between the
-- country_genre_scores aggregate and the artists behind it - the link is
-- computed inside cleansing.merge_genre_signals and was previously discarded
-- the moment the totals were added up.
CREATE TABLE IF NOT EXISTS `{dataset}.country_artist_genres` (
    country_code STRING NOT NULL,
    genre STRING NOT NULL,
    artist_name STRING NOT NULL,
    snapshot_date DATE NOT NULL,
    PRIMARY KEY (country_code, genre, artist_name, snapshot_date) NOT ENFORCED,
    FOREIGN KEY (country_code) REFERENCES `{dataset}.countries` (code) NOT ENFORCED
)
PARTITION BY snapshot_date
CLUSTER BY country_code, genre;

-- Genre reference data (descriptions), from Last.fm's tag.getInfo. Separate
-- from the taxonomy in seeds/genre_buckets.txt because that's a curated list
-- this project controls, whereas this is fetched prose that may be missing.
CREATE TABLE IF NOT EXISTS `{dataset}.genres` (
    genre STRING NOT NULL,
    summary STRING,
    url STRING,
    -- Set even when no description was found, so later runs don't re-spend
    -- API calls retrying the same permanent blanks.
    resolved_at TIMESTAMP,
    PRIMARY KEY (genre) NOT ENFORCED
)
CLUSTER BY genre;

-- THE fact table: one row per track per country per day, carrying additive
-- measures (streams) rather than a score this project invented.
--
-- Everything else here is derived or dimensional. This holds measured
-- quantities from the source at the finest grain available, which is what
-- makes "streams by genre", "domestic share" and "chart churn" all answerable
-- from one table instead of needing a new pipeline each.
--
-- Clustered on artist_name as well as country_code because two of the read
-- queries (hidden_gems, global_artists) scan across countries by artist,
-- which is the access path the Postgres idx_chart_entries_artist served.
CREATE TABLE IF NOT EXISTS `{dataset}.chart_entries` (
    country_code STRING NOT NULL,
    snapshot_date DATE NOT NULL,
    position INT64 NOT NULL,
    artist_name STRING NOT NULL,
    track_name STRING,
    days_on_chart INT64,
    peak_position INT64,
    -- Nullable on purpose: kworb leaves these blank for some entries, and a
    -- missing measure is not the same fact as zero streams.
    daily_streams INT64,
    weekly_streams INT64,
    total_streams INT64,
    -- Position is the natural key within a country-day: a track can't hold
    -- two chart positions, but the same artist legitimately holds several.
    PRIMARY KEY (country_code, snapshot_date, position) NOT ENFORCED,
    FOREIGN KEY (country_code) REFERENCES `{dataset}.countries` (code) NOT ENFORCED
)
PARTITION BY snapshot_date
CLUSTER BY country_code, artist_name;

-- Data-quality check results, one row per check per attempt. Written by
-- scripts/run_validate.py; see app/core/dq.py for what the checks assert.
--
-- The one fact table here that is APPENDED rather than partition-replaced.
-- Every other table answers "what is true for this day", so rewriting the day
-- is correct. This one answers "what happened on each attempt", and a rerun
-- that passes must not erase the attempt that failed - the failures are the
-- part worth keeping, both for debugging and because a threshold guessed from
-- a single observation only becomes a real threshold once there is a range to
-- look at.
--
-- It is also what makes freshness monitorable: MAX(run_ts) answers "is the
-- pipeline still running", which a healthy-looking API serving stale JSON
-- cannot tell anyone.
CREATE TABLE IF NOT EXISTS `{dataset}.dq_runs` (
    run_ts TIMESTAMP NOT NULL,
    snapshot_date DATE NOT NULL,
    check_name STRING NOT NULL,
    -- pass, warn or fail.
    status STRING NOT NULL,
    -- Nullable: a check that could not run has no value, and recording 0 would
    -- be indistinguishable from a check that genuinely measured zero.
    value FLOAT64,
    threshold FLOAT64,
    -- Stored per row rather than read from the code at query time, so history
    -- stays readable after a check is promoted from advisory to blocking.
    blocking BOOL,
    -- Supporting numbers as a JSON string - the raw counts behind a ratio.
    -- STRING rather than BigQuery's JSON type: nothing queries inside it yet,
    -- and a string needs no special handling on the JSON load path.
    context STRING,
    PRIMARY KEY (snapshot_date, run_ts, check_name) NOT ENFORCED
)
PARTITION BY snapshot_date
CLUSTER BY check_name;

-- Per-country cleanse statistics, from run_cleanse's quality report. Loaded by
-- scripts/run_load.py; asserted on by the unclassified_tag_rate check.
--
-- This table exists because the pipeline's headline quality metric was being
-- computed and then discarded. cleansing.merge_genre_signals counts raw tags
-- it could not classify - tags that normalized to nothing, or fell through
-- genre_buckets.bucket_genre to the "other" bucket - but `other` is dropped
-- BEFORE scoring, so those tags never reach country_genre_scores and no query
-- against the warehouse could see the rate at all. It lived in one local JSON
-- file that every cleanse run overwrote.
--
-- Consequence worth remembering: there was no history to backfill from when
-- this table was added. A metric kept outside the warehouse cannot be trended
-- later, only from now on.
CREATE TABLE IF NOT EXISTS `{dataset}.cleanse_quality` (
    country_code STRING NOT NULL,
    snapshot_date DATE NOT NULL,
    artist_count INT64,
    total_genre_tags INT64,
    unclassified_genre_tags INT64,
    -- Stored alongside its own numerator and denominator, and recomputed at
    -- load time rather than copied from the report's rounded value, so the
    -- three always agree.
    -- NULL, not 0, when a country produced no tags at all: "no tags to
    -- classify" is a different fact from "classified all of them".
    unclassified_rate FLOAT64,
    distinct_genres INT64,
    PRIMARY KEY (country_code, snapshot_date) NOT ENFORCED,
    FOREIGN KEY (country_code) REFERENCES `{dataset}.countries` (code) NOT ENFORCED
)
PARTITION BY snapshot_date
CLUSTER BY country_code;

-- MusicBrainz artist catalogue, imported in bulk by
-- scripts/run_import_mb_dump.py from the published JSON dumps.
--
-- A MIRROR of an external source, which is what makes truncate-and-load the
-- right write pattern for it - the exact opposite of `artists`, where
-- merge_dimension protects enrichment columns that several scripts own.
-- Deciding which of those two a table is should happen before choosing how to
-- write it; getting it backwards silently destroys work.
--
-- Not partitioned: there is no date grain, and at a few million rows this is
-- well inside the size where clustering alone is enough.
CREATE TABLE IF NOT EXISTS `{dataset}.mb_artists` (
    mbid STRING NOT NULL,
    name STRING NOT NULL,
    sort_name STRING,
    -- ISO 3166-1 alpha-2, lowercased, matching countries.code. Nullable
    -- because MusicBrainz coverage of smaller artists is genuinely partial -
    -- that is reported as coverage, never filled with a guess.
    country STRING,
    formed_year INT64,
    -- Person, Group, Orchestra, ... Free to carry, and a plausible
    -- tie-breaker when two artists share a name.
    artist_type STRING,
    imported_at TIMESTAMP NOT NULL,
    PRIMARY KEY (mbid) NOT ENFORCED
)
CLUSTER BY mbid;

-- Name-to-MBID index: one row per distinct normalised name an artist answers
-- to, primary names and aliases alike.
--
-- This table is the whole reason the dump beats the API for matching. Chart
-- rows carry whatever spelling a streaming service used, and matching on the
-- primary name alone loses every artist known by another. Flattening aliases
-- into rows turns matching into an equality join rather than a fuzzy scan.
--
-- `match_name` is written by app/services/cleansing.normalize_artist_name at
-- import time, so both sides of the join are normalised by identical code.
-- Normalising one side at load time and the other at query time is how a join
-- silently misses half its rows.
--
-- NOT unique on match_name, deliberately: distinct artists genuinely share
-- names. `is_primary` is the tie-breaker - a primary-name match should beat an
-- alias match, and a tie between two primaries should resolve to nothing
-- rather than to a guess.
--
-- NO FOREIGN KEY to mb_artists, and the reason is worth reading before adding
-- one back. This file declared `FOREIGN KEY (mbid) REFERENCES mb_artists`, and
-- it failed in production with:
--
--     400 Table ...mb_artists does not have Primary Key constraints
--
-- because BOTH of these tables were created by a LOAD JOB, not by this file. A
-- BigQuery load job against a table that does not exist creates it, inferring
-- the schema from the data - so run_import_mb_dump.py brought them into being
-- before run_init_bq ever ran, and the CREATE statements here have been no-ops
-- against a pair of tables that never carried the constraints they declare.
-- The DDL and the warehouse disagreed, silently, for as long as they existed.
--
-- The FK cannot simply be restored, because fixing it means giving the live
-- mb_artists a primary key, and BigQuery's `ALTER TABLE ... ADD PRIMARY KEY`
-- has no IF NOT EXISTS form - it errors the second time it runs. run_init_bq
-- executes this whole file nightly as stage one, so a statement that succeeds
-- once and fails forever after would take the pipeline down every night.
--
-- Dropping the declaration costs nothing real: the key was NOT ENFORCED, so it
-- guaranteed no integrity, and the relationship is documented in this comment
-- instead. The grain is stated above; the join in merges/resolve_artists.sql
-- is what actually depends on it, and that query is tested.
CREATE TABLE IF NOT EXISTS `{dataset}.mb_artist_names` (
    match_name STRING NOT NULL,
    mbid STRING NOT NULL,
    is_primary BOOL NOT NULL
)
CLUSTER BY match_name;

-- COLUMNS ADDED TO EXISTING TABLES.
--
-- `CREATE TABLE IF NOT EXISTS` is a no-op once the table exists, so it cannot
-- add a column to a dataset that has already been created. Editing the CREATE
-- above would work on a fresh clone and silently do nothing in production -
-- which is the worse of the two outcomes, because the code would then write to
-- a column that exists locally and not in the warehouse.
--
-- ALTER TABLE ... ADD COLUMN IF NOT EXISTS is idempotent in the same way the
-- CREATEs are, so run_init_bq can keep running the whole file every night.
-- New columns belong here rather than in the table definition above.

-- The join key for matching an artist against a reference source, written at
-- load time by cleansing.match_key. It is stored rather than derived in the
-- query because the key cannot be reproduced in SQL: it drops trailing
-- collaborators, which no LOWER(TRIM(...)) will do. Both sides of the join
-- must be produced by identical Python, or the join silently misses exactly
-- the messy rows the normaliser exists for.
ALTER TABLE `{dataset}.artists` ADD COLUMN IF NOT EXISTS match_name STRING;

-- Provenance: which path established this artist's origin. 'mb_dump' for the
-- warehouse join against the MusicBrainz mirror, 'api' for the rate-limited
-- per-artist lookup. Worth carrying because the two have different cost and
-- different failure modes, and "where did this value come from" is otherwise
-- unanswerable after the fact.
ALTER TABLE `{dataset}.artists` ADD COLUMN IF NOT EXISTS resolved_by STRING;
