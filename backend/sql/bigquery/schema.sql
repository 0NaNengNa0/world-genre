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
