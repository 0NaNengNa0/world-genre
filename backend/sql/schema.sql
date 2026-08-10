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
