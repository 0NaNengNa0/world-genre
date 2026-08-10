-- Warehouse schema for World Genre. Applied by scripts/run_init_db.py, but
-- kept as plain SQL (not an ORM model or a migration tool like Alembic) so
-- it's directly readable and directly runnable with no Python involved:
--     psql "$DATABASE_URL" -f sql/schema.sql
--
-- Every statement is idempotent (IF NOT EXISTS) - safe to run against an
-- already-initialized database.

CREATE TABLE IF NOT EXISTS countries (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

-- One row per country per day: the total distinct artists resolved that
-- run. Kept separate from country_top_artists (which only stores the top
-- N) because "how many artists did we see" and "which N are worth
-- showing" are different questions - collapsing them into one table would
-- mean either duplicating artist_count onto every top-artist row, or
-- losing it entirely once you go past the top N.
CREATE TABLE IF NOT EXISTS country_snapshots (
    country_code TEXT NOT NULL REFERENCES countries (code),
    snapshot_date DATE NOT NULL,
    artist_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (country_code, snapshot_date)
);

-- One row per (country, genre, day) - the output of
-- cleansing.merge_genre_signals, upserted on load so rerunning the
-- pipeline the same day overwrites that day's numbers instead of
-- duplicating rows. `sources` is a Postgres array rather than a separate
-- join table - genre reconciliation only ever has 1-2 sources
-- (Last.fm/MusicBrainz), so a normalized sources table would be pure
-- overhead here.
CREATE TABLE IF NOT EXISTS country_genre_scores (
    country_code TEXT NOT NULL REFERENCES countries (code),
    genre TEXT NOT NULL,
    score INTEGER NOT NULL,
    -- Two scores per row, not one, because they answer different questions:
    -- `score` is raw popularity, `distinctiveness` is popularity weighted by
    -- inverse document frequency across countries (see
    -- cleansing.score_distinctiveness). Popularity alone is near-identical
    -- everywhere - pop and rock chart in essentially every country - so
    -- ranking by it makes every country look the same. Distinctiveness
    -- cancels that shared baseline. Storing both means either ranking is a
    -- plain ORDER BY rather than a recompute.
    distinctiveness REAL NOT NULL DEFAULT 0,
    sources TEXT[] NOT NULL,
    snapshot_date DATE NOT NULL,
    PRIMARY KEY (country_code, genre, snapshot_date)
);

-- Additive migration for databases created before distinctiveness existed.
-- Cheap no-op once applied; keeps run_init_db safe to rerun on an existing
-- warehouse without a separate migration tool.
ALTER TABLE country_genre_scores
    ADD COLUMN IF NOT EXISTS distinctiveness REAL NOT NULL DEFAULT 0;

-- Speeds up "top genres for country X's latest snapshot" - the API's most
-- common read pattern - and the trending query's window functions, both
-- of which filter/order by exactly these columns.
CREATE INDEX IF NOT EXISTS idx_country_genre_scores_lookup
    ON country_genre_scores (country_code, snapshot_date, score DESC);

CREATE INDEX IF NOT EXISTS idx_country_genre_scores_distinctive
    ON country_genre_scores (country_code, snapshot_date, distinctiveness DESC);

CREATE TABLE IF NOT EXISTS country_top_artists (
    country_code TEXT NOT NULL REFERENCES countries (code),
    artist_name TEXT NOT NULL,
    rank INTEGER NOT NULL,
    snapshot_date DATE NOT NULL,
    PRIMARY KEY (country_code, snapshot_date, rank)
);

-- Artist dimension. Origin and formation year come from MusicBrainz; both
-- are nullable because coverage is genuinely partial - MusicBrainz doesn't
-- know every charting artist, and resolving all of them is rate-limited to
-- ~1 request/second (see scripts/run_extract_artist_meta.py, which fills
-- this in incrementally across runs rather than blocking one run for hours).
--
-- `resolved_at` distinguishes "not looked up yet" from "looked up, and
-- MusicBrainz genuinely has no country for them". Without it every rerun
-- would retry the same permanent misses forever.
CREATE TABLE IF NOT EXISTS artists (
    artist_name TEXT PRIMARY KEY,
    mbid TEXT,
    origin_country TEXT,   -- ISO 3166-1 alpha-2, matching countries.code
    formed_year INTEGER,
    resolved_at TIMESTAMPTZ
);

-- Which artists caused a genre to score in a country. A bridge table between
-- the country_genre_scores aggregate and the artists behind it - the link is
-- computed inside cleansing.merge_genre_signals and was previously discarded
-- the moment the totals were added up, so "who makes Japan's j-pop" needed
-- re-running the whole aggregation to answer.
CREATE TABLE IF NOT EXISTS country_artist_genres (
    country_code TEXT NOT NULL REFERENCES countries (code),
    genre TEXT NOT NULL,
    artist_name TEXT NOT NULL,
    snapshot_date DATE NOT NULL,
    PRIMARY KEY (country_code, genre, artist_name, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_country_artist_genres_lookup
    ON country_artist_genres (country_code, genre, snapshot_date);

-- Genre reference data (descriptions), from Last.fm's tag.getInfo. Separate
-- from the taxonomy in seeds/genre_buckets.txt because that's a curated list
-- this project controls, whereas this is fetched prose that may be missing
-- for a given genre.
CREATE TABLE IF NOT EXISTS genres (
    genre TEXT PRIMARY KEY,
    summary TEXT,
    url TEXT,
    -- Set even when no description was found, so later runs don't re-spend
    -- API calls retrying the same permanent blanks.
    resolved_at TIMESTAMPTZ
);

-- THE fact table: one row per track per country per day, carrying additive
-- measures (streams) rather than a score this project invented.
--
-- Everything else here is derived or dimensional - country_genre_scores
-- holds computed weights, country_top_artists holds a ranking. This holds
-- measured quantities from the source, at the finest grain available, which
-- is what makes "streams by genre", "domestic share" and "chart churn" all
-- answerable from one table instead of needing a new pipeline each.
CREATE TABLE IF NOT EXISTS chart_entries (
    country_code TEXT NOT NULL REFERENCES countries (code),
    snapshot_date DATE NOT NULL,
    position INTEGER NOT NULL,
    artist_name TEXT NOT NULL,
    track_name TEXT,
    days_on_chart INTEGER,
    peak_position INTEGER,
    -- Nullable on purpose: kworb leaves these blank for some entries, and a
    -- missing measure is not the same fact as zero streams.
    daily_streams BIGINT,
    weekly_streams BIGINT,
    total_streams BIGINT,
    -- Position is the natural key within a country-day: a track can't hold
    -- two chart positions, but the same artist legitimately holds several.
    PRIMARY KEY (country_code, snapshot_date, position)
);

CREATE INDEX IF NOT EXISTS idx_chart_entries_artist
    ON chart_entries (artist_name);

CREATE INDEX IF NOT EXISTS idx_chart_entries_latest
    ON chart_entries (country_code, snapshot_date DESC);
