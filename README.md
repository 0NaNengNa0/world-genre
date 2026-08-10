# World Genre

Full-stack data project: scrape music chart data per country, cleanse and reconcile it, serve it to a React frontend.

The UI opens on a **clickable world map**, shaded by dominant genre or by domestic streaming share. Clicking a country pops up its top 5 artists and genres; "see more" opens the full breakdown — charting tracks with daily stream counts, domestic-vs-imported share, the ranked artist list, and a genre donut toggleable between **real stats** (share of what's actually played) and **TF-IDF** (share of what makes that country different). Four more views sit behind one toggle: **Grid**, **Trends** (genres rising or falling week over week), **Global artists** (biggest acts worldwide by summed chart streams) and **Compare** (two countries quantified against each other).

Compare reports a real overlap figure using **histogram intersection** — for each genre, the smaller of the two countries' shares, summed. If both spend 20% on pop that's 20 points of genuine overlap; if one spends 30% and the other 5%, only 5 points are shared. Comparing genre *names* instead would call two countries identical whether pop is 40% of one and 2% of the other. Genres absent from one side count as 0% there rather than being skipped, since that's usually the most revealing row in the table.

- **Frontend**: Vite + React (TypeScript)
- **Backend API**: FastAPI (serves data to the frontend)
- **Batch pipelines**: Airflow (extracts, cleanses, loads on a schedule)
- **Shared code**: `backend/app/services/` — imported by both FastAPI and Airflow

## Repo layout

```
world-genre/
├── package.json                 # root-level scripts: npm run start / dev / stop
├── scripts/
│   └── trigger-genre-pipeline.js
├── frontend/                    # Vite React app
└── backend/
    ├── app/
    │   ├── main.py              # FastAPI entrypoint
    │   ├── api/routes/          # HTTP endpoints
    │   ├── schemas/             # Pydantic models
    │   ├── services/            # business logic (imported by API + Airflow)
    │   │   ├── extractors/      # one module per data source
    │   │   ├── cleansing.py     # genre normalization + Last.fm/MusicBrainz merge
    │   │   ├── genre_buckets.py # collapses ~2,200 genres onto a <200-bucket taxonomy
    │   │   └── countries.py     # reads from Postgres for the API
    │   └── core/
    │       ├── config.py        # settings, seed loaders
    │       └── db.py            # Postgres connection helper
    ├── seeds/                   # static reference data
    │   ├── countries.csv        # 76 countries; generated, not hand-edited
    │   ├── genre_buckets.txt    # curated <200-genre taxonomy
    │   └── musicbrainz_genres.txt  # ~2,200-genre canonical list (fetched, not hand-written)
    ├── sql/
    │   ├── schema.sql           # warehouse tables - see Warehouse section below
    │   └── queries/              # example/reporting queries, incl. the trending endpoint's
    ├── scripts/                 # runnable entrypoints (dev / debug / Airflow tasks)
    ├── dags/
    │   └── genre_pipeline_dag.py
    ├── tests/                   # pytest - see Testing section below
    ├── data/                    # gitignored: raw/, processed/ (latest + history/ + quality report)
    ├── docker-compose.yaml      # Airflow stack + app-postgres
    ├── ruff.toml
    ├── .env.example
    └── requirements.txt
├── .github/workflows/
│   └── backend-ci.yml           # lint + test on every backend push/PR
```

---

## Prerequisites

| Tool           | Version  | Mac / Linux                      | Windows                                                          |
| -------------- | -------- | -------------------------------- | ---------------------------------------------------------------- |
| Python         | 3.11+    | `brew install python@3.12`       | [python.org installer](https://www.python.org/downloads/) — check "Add to PATH" |
| Node           | 20+      | `brew install node`              | [nodejs.org installer](https://nodejs.org/)                      |
| Docker Desktop | latest   | [docker.com](https://www.docker.com/products/docker-desktop) | [docker.com](https://www.docker.com/products/docker-desktop) (enable WSL 2 backend) |
| Git            | any      | preinstalled / `brew install git`| [git-scm.com](https://git-scm.com/download/win)                  |

> Windows users: install [WSL 2](https://learn.microsoft.com/en-us/windows/wsl/install) and run all commands below inside Ubuntu for the closest experience to Mac/Linux. The alternative (PowerShell + native Windows Python), which this README's Windows commands assume, works fine too — just note path separators differ (`.venv\Scripts\...`, not `.venv/bin/...`).

---

## First-time setup

```bash
git clone <repo-url> world-genre
cd world-genre
```

### Python venv (repo root — not inside `backend/`)

The venv lives at the **repo root**, one level above `backend/`, even though the code it serves is in `backend/`. This matters: a venv baked with an absolute path to one location breaks if the folder is later moved or copied elsewhere (`pip`/`python` launchers hardcode that path) — so always create it fresh in place rather than copying one from another folder.

**Mac / Linux / WSL:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

**Windows PowerShell:**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
```

If PowerShell blocks the activate script, run once as admin: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

### Backend env vars

```bash
cd backend
cp .env.example .env      # then fill in LASTFM_API_KEY
cd ..
```

MusicBrainz and Deezer need no key. See [Environment variables](#environment-variables) below for details.

### Frontend + root tooling

```bash
npm install                 # root: installs concurrently + kill-port, used by npm run start/stop
npm --prefix frontend install
```

### One-time: canonical genre list + Airflow init + warehouse schema

```bash
cd backend
.venv-relative-python -m scripts.fetch_musicbrainz_genres   # e.g. ..\.venv\Scripts\python -m scripts.fetch_musicbrainz_genres
docker compose up airflow-init      # one-time: init the Airflow metadata DB + admin user
docker compose up -d app-postgres --wait   # one-time: bring up the warehouse so the next line can reach it
.venv-relative-python -m scripts.run_init_db   # one-time: create the warehouse tables (sql/schema.sql)
cd ..
```

The genre list is a reference table (like a small dataset download), not something the recurring pipeline re-fetches — rerun it occasionally to pick up genres MusicBrainz has added, not on every pipeline run. `run_init_db` is safe to rerun any time (every statement is `CREATE TABLE IF NOT EXISTS`) — it's only listed as "one-time" because there's normally no reason to.

---

## Running in development

### One command (recommended)

```bash
npm run start
```

Brings up Airflow + the warehouse (`app-postgres`), triggers the `genre_pipeline` DAG once (retrying for up to a minute while Airflow finishes starting up), and runs the frontend + backend together — all under one process tree, labeled and colored per source in a single terminal. `Ctrl+C` once stops all of it, Postgres included.

- Frontend: `http://localhost:5173`
- Backend API: `http://localhost:8000` (docs at `/docs`, health at `/api/health`, one country at `/api/countries/jp`)
- Airflow UI: `http://localhost:8080` (login `airflow` / `airflow`)
- Warehouse: `postgresql://world_genre:world_genre@localhost:5433/world_genre` (e.g. via `psql` or a GUI client)

```bash
npm run dev     # frontend + backend only, skip Airflow (e.g. if it's already running)
npm run stop    # docker compose down + force-frees ports 5173/8000
```

Both `start` and `dev` free ports 5173 and 8000 first, via npm's `prestart`/`predev` hooks. That isn't belt-and-braces: on Windows, `Ctrl+C` makes cmd.exe ask *"Terminate batch job (Y/N)?"*, and the wrapper can exit while a child still holds its port — so the next run fails to bind and only the run after that works. Clearing the ports up front makes the first attempt the one that works.

**`start` deliberately runs uvicorn without `--reload`; `dev` keeps it.** On Windows the reloader's restart tears down sibling processes sharing the console — editing a file under `backend/app/` while `start` was running was reliably killing Airflow, Vite and the backend together, with no Ctrl+C typed. `start` is the whole-stack command where a file save must not stop your containers; `dev` is the tight loop where reload is the point and there's nothing expensive to lose.

So: **iterate on backend code with `npm run dev`** (bring Airflow up separately with `npm run airflow:up` if you need it), and use `npm run start` to run everything.

### Manual (three terminals) — useful for debugging one piece in isolation

**Terminal 1 — Frontend**
```bash
cd frontend
npm run dev
```
Vite proxies `/api/*` to the backend (see `frontend/vite.config.ts`).

**Terminal 2 — Backend API**
```bash
# from repo root, venv activated
uvicorn app.main:app --reload --port 8000 --app-dir backend
```

**Terminal 3 — Airflow**
```bash
cd backend
docker compose up -d
```
- Status: `docker compose ps` · Logs: `docker compose logs -f` · Stop: `docker compose down` · Wipe state: `docker compose down -v`

The compose file mounts `./app`, `./scripts`, `./seeds`, and `./data` into the containers (plus `PYTHONPATH=/opt/airflow`) so DAG tasks can `from app.services.extractors import ...` and `from scripts.run_extract_x import main` without a rebuild.

---

## The data pipeline

Five sources feed into cleansing, which feeds into the warehouse — extract → transform → load:

| Step | Script | Reads | Writes |
| --- | --- | --- | --- |
| Extract | `scripts/run_extract_kworb.py` | kworb.net (scrape) | `data/raw/kworb/{code}.json` |
| Extract | `scripts/run_extract_lastfm.py` | Last.fm API | `data/raw/lastfm/{code}.json` |
| Extract | `scripts/run_extract_musicbrainz.py` | MusicBrainz API | `data/raw/musicbrainz/{code}.json` |
| Extract | `scripts/run_extract_deezer.py` | Deezer API (images only) | `data/raw/deezer/artists.json` |
| Extract | `scripts/run_extract_wikidata.py` | Wikidata/Commons (images only) | `data/raw/wikidata/artists.json` |
| Cleanse | `scripts/run_cleanse.py` | all of the above | `data/processed/{code}.json` |
| Load | `scripts/run_load.py` | `data/processed/{code}.json` + `data/raw/kworb/` | Postgres (`countries`, `country_snapshots`, `country_genre_scores`, `country_top_artists`, `chart_entries`, `artists`) |
| Enrich | `scripts/run_extract_artist_meta.py` | MusicBrainz API | `artists.origin_country`, `artists.formed_year` |

The cleanse step (`app/services/cleansing.py`) does four things the raw extractors deliberately don't:
1. **Normalizes genre spelling** — "hiphop"/"hip hop"/"rap" all resolve to one form, fuzzy-matched against MusicBrainz's canonical ~2,200-genre list (`seeds/musicbrainz_genres.txt`).
2. **Buckets onto a coarser taxonomy** — `app/services/genre_buckets.py` collapses that onto `seeds/genre_buckets.txt` (<200 broad genres), so "chicago drill" and "trap" both count toward something meaningful to compare across countries, instead of staying too fine-grained to ever overlap.
3. **Reconciles Last.fm + MusicBrainz** — both sources vote into the same bucket rather than one silently falling back to the other; each result's `sources` field shows whether Last.fm, MusicBrainz, or both agreed.
4. **Scores distinctiveness** — see below.

### Why popularity alone isn't enough

Ranking genres by raw popularity makes every country look identical. Measured across the first 20 countries: `pop` and `rock` appeared in **all 20** top-5s, `pop` led **16 of 20**, and only **9 distinct genres** appeared anywhere. A dashboard about genre *differences* was reporting that everyone likes pop.

That's inherent to chart data — it measures commercial reach, and global superstars chart identically everywhere. Two changes address it:

**Sample deeper.** `run_extract_lastfm.py` fetches 100 artists per country, not 20. At 20, a country's chart is ~80% global artists (Brazil's top 20 had 3 Brazilian acts; Mexico's had 6 BTS entries and 2 Latin ones). The artists that differentiate a country live *below* the global layer.

**Rank by distinctiveness, not just volume.** `cleansing.score_distinctiveness` weights each genre by inverse document frequency across countries — the TF-IDF idea, where each country is a document:

```
distinctiveness = score × log(total_countries / countries_with_that_genre)
```

A genre in every country scores `log(1) = 0` and drops out; one unique to a single country gets the largest multiplier. So filtering out generic pop needs **no hardcoded artist or genre blocklist** — the cross-country comparison decides what counts as generic, and it stays correct as countries are added.

Two details that turned out to matter more than expected, both learned by breaking them:

- **Document frequency counts *meaningful* presence, not bare presence.** With 100 artists sampled per country, a single stray tag puts a genre in a country's distribution. `j-pop` reached a document frequency of 20/20 and scored **zero** distinctiveness for Japan — while being 11% of what Japan actually plays. A genre now has to be ≥1% of a country's weight before that country counts toward its document frequency.
- **The noise floor is a share, not a raw score.** An absolute floor silently stops filtering when sample depth changes: at 20 artists a country's weights totalled ~250, at 100 they total ~1,400, so a fixed floor of 3 slid from ~1.2% to ~0.2%. Japan briefly ranked `bossa nova` — 0.4% of its listening — as its single most distinctive genre, purely because few other countries registered it at all. Rarity alone isn't evidence.

Both scores are kept, because they answer different questions and suppressing popularity would misrepresent what people actually play:

| Country | Real stats (share of plays) | TF-IDF (share of what's distinctive) |
| --- | --- | --- |
| Japan | rock 20.9, pop 14.4, j-pop 11.3 | **j-pop 79.1**, k-pop 9.3, jazz 3.5 |
| Mexico | pop 20.0, rock 11.3, k-pop 8.7 | **latin 35.8**, **reggaeton 25.3**, k-pop 17.3 |
| India | hip-hop, pop, alternative, rock | **bollywood**, k-pop, jazz |

Note how little the left column varies between countries and how much the right one does — that gap is the entire reason the second ranking exists.

Spotify was dropped as a source entirely (it stopped exposing chart/genre data to third-party dev-mode apps in Feb 2026) — `app/services/extractors/spotify.py` and `scripts/run_extract_spotify.py` are unused, safe to delete.

### Country coverage (and its limits)

**76 countries**, which is every market kworb publishes a Spotify chart for. That's a hard ceiling imposed by the source, and the coverage is uneven in ways worth stating outright rather than leaving a reader to notice: 34 European markets but only **4 African** ones (Egypt, Morocco, Nigeria, South Africa), and **no China**. Any "global" conclusion from this data is really a conclusion about Spotify's reporting markets.

`seeds/countries.csv` is **generated, not hand-edited** — rerun `python -m scripts.generate_countries_seed` after changing the country list. The reason it's generated: Last.fm's `geo.getTopArtists` matches on exact ISO 3166-1 names and fails *silently* on anything else (HTTP 200, empty artist list), so `"South Korea"` costs that country all its data while `"Korea, Republic of"` works. The CSV therefore carries both forms:

| `lastfm_name` (API) | `kworb_code` | `country_name` (display) |
| --- | --- | --- |
| `Korea, Republic of` | `kr` | South Korea |
| `Viet Nam` | `vn` | Vietnam |
| `Bolivia, Plurinational State of` | `bo` | Bolivia |

The API names come from `pycountry` rather than being typed by hand. Where the current ISO name *isn't* what Last.fm accepts, `LASTFM_NAME_OVERRIDES` in the generator holds the exception with its reason — currently one entry: ISO renamed Turkey to Türkiye in 2022, but Last.fm still matches `"Turkey"`.

Because a wrong name fails silently, `run_extract_lastfm.py` reports any country that returns zero artists, and the quality report flags zero-artist countries. That's the intended way to catch a bad name — the loud failure the API doesn't give you.

### Artist images: two sources, in order

Deezer is primary, Wikidata/Wikimedia Commons fills its gaps. Both are needed because each fails where the other works.

**Deezer never returns an empty picture field.** For artists it has no photo of, it returns a well-formed CDN URL whose path is the MD5 of the empty string — so a truthiness check accepts it and the browser renders a blank square. That hit **55 of 767 artists**, including Radiohead, Coldplay, The Weeknd and Kendrick Lamar. `deezer.has_real_picture` detects that placeholder; everything downstream tests with it rather than `if url`.

**Wikidata joins on an identifier, not a name.** Property `P434` *is* the MusicBrainz artist ID, and Last.fm already supplies an mbid per artist — so there's no fuzzy name matching, which is separately how Deezer sometimes returns the *wrong* artist rather than none. `P18` gives the image. One SPARQL query resolves hundreds of MBIDs at once, so on this dataset (184 artists lacking a Deezer photo) the whole step is a **single HTTP request** — nothing like MusicBrainz's ~1 req/sec crawl.

Order matters and isn't arbitrary: Commons requires a free licence, and most commercial promo photos aren't, so its coverage skews older and more Western — expect misses for K-pop and J-pop especially. Deezer covers current chart artists far better. Hence Deezer first, Wikidata only for what's missing. The Commons images that do resolve are freely licensed, which is the safer footing for a publicly linked site than rehosting a streaming service's assets.

Two implementation notes: `P18` returns the **original** file, routinely multiple megabytes, so URLs go through `Special:FilePath?width=250`; and Wikidata still emits `http://`, which a browser blocks as mixed content on an https page, so it's upgraded to `https://`.

### Data quality report + history

Every `run_cleanse.py` run also writes:

- `data/processed/_quality_report.json` — per-country artist counts and genre-tag "unclassified rate" (the % of raw tags that were junk, or didn't map onto any bucket including the `"other"` catch-all), plus a summary flagging any country with zero artists or an unusually high unclassified rate. This is what would have caught the South Korea empty-data bug automatically instead of it needing to be spotted by eye.
- `data/processed/history/{code}/{date}.json` — a dated snapshot alongside the "latest" file the API reads, so genre trends are reconstructable over time instead of only ever having a single point-in-time view.

Both are gitignored (same as the rest of `data/processed/`) since they're generated output, not source.

### Running it manually (without Airflow)

```bash
python -m scripts.run_extract_kworb
python -m scripts.run_extract_lastfm
python -m scripts.run_extract_musicbrainz
python -m scripts.run_extract_deezer
python -m scripts.run_extract_wikidata   # fills the artist photos Deezer lacks
python -m scripts.run_cleanse
python -m scripts.run_load       # needs app-postgres up and run_init_db already applied
python -m scripts.run_extract_artist_meta   # fills artist origins, 250 per run
```

### DAG (`dags/genre_pipeline_dag.py`)

```
kworb ──┬──> musicbrainz ──────> cleanse ──┬──> load ──> enrich_artists
lastfm ─┘                                  │
kworb ──┬──> deezer ──> wikidata      ensure_schema
lastfm ─┘
```
`kworb` and `lastfm` run in parallel with no dependencies; `musicbrainz` and `deezer` each need both (Last.fm for artist MBIDs/names, kworb as fallback); `wikidata` runs strictly after `deezer` because it only looks up the artists Deezer found no photo for; `cleanse` waits on `kworb`, `lastfm`, and `musicbrainz` (not the image tasks, which feed `app/services/countries.py` directly); `load` waits on `cleanse` and `ensure_schema`; `enrich_artists` runs last, because its worklist is the `artists` rows `load` just wrote.

---

## Warehouse (Postgres)

`scripts/run_load.py` is the pipeline's "load" stage - it upserts each day's cleansed output into Postgres, and the FastAPI backend reads from there (`app/services/countries.py`), not from `data/processed/*.json` directly. Schema (`backend/sql/schema.sql`):

| Table | Grain | Notes |
| --- | --- | --- |
| `countries` | one row per country | code, display name |
| `country_snapshots` | one row per (country, day) | `artist_count` |
| `chart_entries` | one row per (country, position, day) | **the fact table** — `artist_name`, `track_name`, `daily/weekly/total_streams`, `days_on_chart`, `peak_position` |
| `artists` | one row per artist | dimension — `mbid`, `origin_country`, `formed_year`, `resolved_at` |
| `country_genre_scores` | one row per (country, genre, day) | `score` (popularity), `distinctiveness` (IDF), `sources` (array) - this is the time series |
| `country_top_artists` | one row per (country, day, rank) | top 5 artists that day |

Every load is an upsert keyed on `(..., snapshot_date)`, so rerunning the pipeline the same day overwrites that day's numbers instead of duplicating rows.

### The fact table

`chart_entries` holds **measured quantities from the source** — stream counts, chart position, days on chart — at the finest grain available: one row per track, per country, per day. Everything else in the warehouse is derived (`country_genre_scores` holds weights this project computes) or dimensional (`countries`, `artists`). That split is what makes "streams by genre", "domestic share" and "chart churn" all answerable from one table rather than needing a new pipeline each.

It came from data already being downloaded. The scraper pulls **11 columns from kworb and the pipeline used exactly one** — `row[2]`, the artist-song string. Position, peak, longevity and all four stream columns were written to disk and ignored. `kworb.parse_chart_entry` now interprets the layout once, in one place, instead of three separate files re-deriving `row[2]`.

### Domestic vs imported

`artists.origin_country` comes from MusicBrainz — the same artist lookup `get_genres` already makes, so it costs no new integration. Joined against `chart_entries`, it answers what share of a country's streaming goes to its own artists (`sql/queries/domestic_share.sql`, surfaced on the country detail endpoint).

Two decisions worth knowing:

**Weighted by streams, not row count.** Three small domestic tracks and one global hit are not a 75/25 split of a country's listening; by streams it can be 10/90. There's a test pinning exactly that case.

**Coverage is reported alongside the answer.** MusicBrainz doesn't know an origin for every charting artist, so the share is computed over the streams that *can* be attributed — and 40% domestic means something very different at 90% coverage than at 15%. The API returns `coverage_percentage` next to `domestic_percentage`, and returns `null` rather than "0% domestic" when nothing is resolved yet. Reporting the denominator is the difference between a statistic and a guess.

**Enrichment is bounded per run.** Resolving every charting artist would take roughly **6 hours** at 76 countries — MusicBrainz allows ~1 request/second and most chart artists have no known MBID, so they need a name search first (2 calls each). `run_extract_artist_meta.py` therefore resolves at most 250 per run, most-charted first, and records `resolved_at` even on a genuine miss so later runs don't re-spend their budget on the same blanks. The dimension fills in across weekly runs instead of any single run becoming an outage.

### Genre scoring

Genres are stored in **full** (every genre a country has, ~30–60 of them), not just the handful any one view shows. Two reasons: percentage share is only correct when divided by the true total, and "top 5 by popularity" vs "top 10 by distinctiveness" then differ by an `ORDER BY`/`LIMIT` instead of being baked in at write time — where changing a view would mean re-running an hour-long pipeline.

Three queries live in `backend/sql/queries/`, runnable directly with `psql "$DATABASE_URL" -f sql/queries/<file>.sql` or through Python:

- **`country_genre_shares.sql`** — each genre's share of a country's total genre weight, via `SUM(score) OVER ()` computed *before* the `LIMIT` so the slices plus the leftover "other" total 100. Backs the detail view's pie chart at `GET /api/countries/{code}`.
- **`trending_genres.sql`** — genre score deltas between each country's two most recent load dates, using `LAG()` over a window. Wired up at `GET /api/genres/trending`. Needs at least two days of pipeline history to return anything — with a fresh database it legitimately returns `[]`, not a bug.
- **`hidden_gems.sql`** — artists a country streams heavily that barely chart anywhere else. Same IDF idea as the genre scoring, applied to artists: `streams_here × LN(total_countries / countries_charting_them)`. An artist charting everywhere scores `LN(1) = 0` and drops out **by construction**, so global superstars can't appear no matter how many streams they have — no blocklist involved. On real data this surfaces back number and Aimyon for Japan, sertanejo and funk carioca acts for Brazil, and the corridos tumbados scene for Mexico.
- **`global_artists.sql`** — biggest artists worldwide by streams summed across every chart, with how many countries they chart in and the change since the previous run. Uses `DENSE_RANK` per country rather than a single global "latest date", because countries aren't guaranteed to load on the same day and a shared date would silently drop any that ran late.
- **`similar_countries.sql`** — which country pairs share the most top genres, via a self-join. Not wired to an endpoint; kept as a documented ad hoc analytical query, which is a normal part of the job too, not everything needs a route.

> Gotcha worth knowing if you edit these: psycopg2 scans the whole `.sql` file for parameter placeholders **without stripping SQL comments**, so a bare `%` character even inside a `--` comment raises `argument formats can't be mixed`. Spell the word out instead.

### Query design

`GET /api/countries` renders 76 cards, and the obvious implementation — loop the countries, fetch each one's genres and artists — issued **305 queries per request**, each re-deriving that country's latest `snapshot_date` with a correlated subquery. It now uses `ROW_NUMBER() OVER (PARTITION BY country_code ...)` to rank every country's genres in one pass, so the endpoint is **3 queries regardless of how many countries exist**: 66 ms → 19 ms locally, and a much bigger gap against a networked database where per-round-trip latency dominates rather than query time.

The same endpoint also used to return all 100 artists per country — about 7,600 strings for a grid that displays three each. It now returns 5, scanning 20 server-side to pick a cover image (roughly 7% of artists have no Deezer photo, so taking only the first would show a placeholder whenever artist #1 is one of them).

**Connections come from a pool** (`psycopg2.pool.ThreadedConnectionPool`), not one per call. Measured: **2.30 ms to open a connection vs 0.10 ms to reuse one** — a 23× overhead on an endpoint whose queries take under a millisecond, and that was the best case over a unix socket with no TLS. A managed Postgres adds network round-trips and a handshake to every one. The detail endpoint issues 5 queries, so unpooled it paid that setup cost five times before running any SQL.

---

## The map

Deliberately **no mapping library** — no react-simple-maps, d3-geo or Leaflet. The map is plain SVG:

- `lib/geo.ts` projects lon/lat with a hand-rolled equirectangular transform (one linear formula) and turns GeoJSON polygons into SVG paths.
- Country outlines are fetched at runtime rather than bundled, since a few hundred KB of geometry has nothing to do with the app's code and never changes. To drop the runtime CDN dependency, download the GeoJSON into `frontend/public/` and point `GEOJSON_URL` in `WorldMap.tsx` at it.

The trade-off worth naming: equirectangular badly distorts area near the poles, so Greenland and Antarctica look enormous. For "click your country" that's fine, and no country in this dataset sits far enough north or south for it to affect clicking.

**Zoom and pan** are a `translate/scale` transform on a wrapping `<g>`, not a mutated `viewBox` — which means CSS can animate it for free. Scroll wheel zooms toward the cursor, `+`/`−` zoom about the centre, dragging pans, and clicking a country flies to fit it. Button- and selection-driven moves animate; wheel and drag don't, because easing those leaves the map trailing a frame behind the pointer. Panning is clamped so the viewport always stays covered, and borders use `vector-effect="non-scaling-stroke"` so they stay hairline instead of thickening into bands at 40× zoom.

Zoom-to-fit uses the bounds of a country's **largest** polygon, not of all its territory. That's not a detail — Russia's Chukotka crosses the antimeridian and the US has Alaska and Hawaii, so a box around everything spans the whole map and "fitting" it zooms back out to the world view. Measured on a Russia-shaped case: all polygons give a fit scale of 1.00 (no zoom whatsoever), the largest gives 1.56 and frames the mainland.

**Matching map shapes to countries is the fiddly part**, because sources disagree on where a country's identity lives — `world.geo.json` uses an alpha-3 feature `id`, `world-atlas` a numeric one, Natural Earth `properties.ISO_A2`/`ISO_A3`. `countryCodeOf` accepts all three, so swapping map sources means changing one URL rather than rewriting the lookup. `lib/isoCodes.ts` holds the alpha-2 ↔ alpha-3 ↔ numeric table and is **generated** (`python -m scripts.generate_iso_codes`) from pycountry — hand-typing 76 country codes is the same error class that made South Korea silently return no data, and a wrong code here fails just as quietly, as a country that never highlights.

Countries with no data are drawn as context but are `pointer-events: none`, so the map doesn't invite clicks that do nothing. The caption under the map reports how many countries matched, which is the quickest way to spot a map source whose ids don't line up.

**Shading** (`lib/mapColors.ts`) has two modes, and the encoding differs deliberately. Dominant genre uses a **qualitative** palette — genres are categories with no ordering, so a gradient would imply a ranking that doesn't exist. Domestic share uses a **sequential** ramp, because it is ordered. "No data" is a grey held visually apart from every data colour, so an absent value never reads as a low one — the most common way a choropleth misleads.

Domestic mode also **withholds colour below 20% coverage**. A share computed from 2% of a country's streams isn't a measurement, and shading it the same as one computed from 90% would let the map assert something the data can't support.

## Testing + CI

```bash
cd backend
pip install -r requirements-dev.txt   # pytest + ruff + pgserver, dev-only
pytest -v
ruff check .
```

> Three requirements files, each with a distinct consumer: **`requirements.txt`** is what you install locally (everything), **`requirements-pipeline.txt`** is the subset installed into the Airflow image — the API's FastAPI/pydantic must stay out of it, since Airflow 3 ships its own and conflicting pins break its api-server — and **`requirements-dev.txt`** is test/lint tooling that ships to neither.

Tests live in `backend/tests/`:

- `test_cleansing.py`, `test_genre_buckets.py` — the pure-function core of the cleansing pipeline, against the real seed data (not mocks) — genre normalization only matters insofar as it handles real messy text, so these run against the actual ~2,200-genre MusicBrainz list and the actual 150-entry bucket taxonomy this project ships with.
- `test_db_integration.py` — schema setup, loading, distinctiveness persistence, and the trending query against a **real** Postgres, not a mock connection. That Postgres is provisioned by the `pgserver` package (a real Postgres binary bundled in a pip install) via the `pg_database_url` fixture in `conftest.py` - no Docker or system Postgres install needed to run the test suite, including in CI.
- `test_countries_seed.py` — the generated country seed, focused on the `lastfm_name` column, since a wrong value there silently costs a country all its data.
- `test_extractors.py` — the extraction layer against fixtures shaped like real responses, no network. This is the only part of the pipeline whose input someone else controls, and it fails *silently*: kworb changing its markup or Last.fm renaming a field yields zero rows rather than an error, and the first symptom is an empty dashboard days later. Includes a test that deliberately documents a known fragility rather than a desired behaviour — the scraper binds to the *first* `<table>` on the page, so a layout change above the chart would return junk instead of failing.
- `test_health.py`, `test_error_handling.py` — that the health check can actually fail, that a database outage returns 503 rather than 500, and that neither ever echoes psycopg2's error text, which embeds host, port and username.

`.github/workflows/backend-ci.yml` runs lint then the full test suite (DB tests included) on every push/PR that touches `backend/`, same commands as above.

---

## Environment variables

All secrets live in `backend/.env` (gitignored). Template in `backend/.env.example`.

| Variable                     | Used by            | Where to get it |
| ----------------------------- | ------------------- | ---------------- |
| `LASTFM_API_KEY`              | Last.fm extractor   | [last.fm/api/account/create](https://www.last.fm/api/account/create) |
| `API_HOST`, `API_PORT`        | FastAPI             | defaults are fine |
| `DATABASE_URL`                 | FastAPI, `run_load.py`, `run_init_db.py` | defaults to `localhost:5433` (the `app-postgres` service's host-mapped port) — only change this if you're pointing at a different Postgres. Airflow containers override this automatically to reach the same database by service name; see `docker-compose.yaml`. |
| `CORS_ORIGINS`                 | FastAPI             | comma-separated allowed origins; defaults to `http://localhost:5173`. Set this to your real frontend origin when deploying. |
| `LOG_LEVEL`                    | FastAPI             | defaults to `INFO`; set `DEBUG` to trace a misbehaving deployment without a code change. |

> `_PIP_ADDITIONAL_REQUIREMENTS` is deliberately **not** set. It once installed the extractors' dependencies into the Airflow containers at startup, but `backend/Dockerfile` now bakes them into the image — leaving it set meant five containers each re-installing the same four packages on every start, for no benefit.

MusicBrainz and Deezer need no key at all. For Airflow specifically, prefer setting secrets in the Airflow UI (Admin → Variables) over `.env` if this ever goes beyond local dev.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'app'` when running scripts**
Run scripts as modules from `backend/`: `python -m scripts.run_extract_kworb`, not `python scripts/run_extract_kworb.py`.

**An endpoint returns 503 with "The database is currently unavailable"**
Deliberate, not a bug: a dependency being down is something the caller can retry, so it's a 503 rather than a 500 telling clients and monitors that the API itself is broken. The `error` field names the exception class; the full reason is in the API log (psycopg2's message embeds host, port and username, so it's never echoed to the response).

**`/api/health` returns 503 with `"status": "degraded"`**
Working as intended — the API is up but Postgres isn't reachable. `database` names the failure. Check `docker compose ps` for `app-postgres`, and see the connection-refused entry below.

**Backend returns 404 on `/api/health`**
Uvicorn didn't reload after an edit. Save the file again, or restart it.

**Everything shut down on its own, without pressing Ctrl+C**
Look at the first line of the log. If it says `WatchFiles detected changes … Reloading`, a backend file was edited while `start` was running, and uvicorn's reloader took its console siblings down with it — Airflow included. That's why `start` no longer passes `--reload`. If you're seeing it, you're on an older `package.json`; pull the current one, or use `npm run dev` for backend iteration.

**`npm run start` only works on the second attempt (Windows)**
Should be fixed by the `prestart` hook, but the underlying cause is worth knowing: `Ctrl+C` on Windows prompts *"Terminate batch job (Y/N)?"* from cmd.exe, and answering **N** leaves the batch wrapper alive while its children have already been signalled — a reliable way to orphan whatever was holding port 5173 or 8000. Answer **Y**. If a run still fails to bind, `npm run stop` clears both ports.

**Frontend shows "Failed to fetch countries (502)"**
The backend isn't running (or crashed) — a 502 means Vite's dev proxy couldn't reach anything on `localhost:8000` at all, not that the backend returned an error. Confirm with `curl http://localhost:8000/api/health`; if that also fails, start/restart uvicorn.

**Airflow containers exit immediately**
Run `docker compose logs airflow-init` — usually a permissions issue on Linux. Ensure `AIRFLOW_UID` is set in `.env`: `echo "AIRFLOW_UID=$(id -u)" >> .env`. Not needed on Windows/WSL2.

**MusicBrainz extractor hitting repeated 503s**
MusicBrainz's public API allows ~1 request/second, strictly enforced. `app/services/extractors/musicbrainz.py` retries with backoff automatically; if you're still seeing sustained 503s, you may be running the extractor from two places at once (e.g. Airflow and a manual run) competing for the same rate limit.

**The MusicBrainz task takes a very long time on first run**
Expected. At 76 countries × 100 artists the unique-artist queue is large, and MusicBrainz is capped at ~1 req/sec, so a cold run can take upwards of an hour. It only happens once — every resolved artist is cached to `data/raw/musicbrainz/_artists/` and skipped on later runs, and network failures are deliberately *not* cached so they retry rather than being remembered as "no genres".

**A country shows no genres / is missing from the frontend**
Almost always a `lastfm_name` mismatch — Last.fm returns HTTP 200 with an empty list for an unrecognized country name, so nothing raises. Check the `run_extract_lastfm` logs for the "returned no artists" warning and `data/processed/_quality_report.json` for `zero_artist_countries`, then fix the name via `LASTFM_NAME_OVERRIDES` in `scripts/generate_countries_seed.py` and regenerate (editing the CSV directly gets overwritten on the next regeneration).

**`psycopg2.OperationalError: connection refused` (or similar) from the API or `run_load.py`**
`app-postgres` isn't up yet, or hasn't finished its healthcheck. `npm run start` waits for it, but if you're running things manually: `cd backend && docker compose up -d app-postgres --wait`.

**`psycopg2.errors.UndefinedTable: relation "countries" does not exist`**
The schema hasn't been applied yet - run the one-time `python -m scripts.run_init_db` step (see First-time setup). Safe to rerun any time.

**`/api/countries` returns an empty list**
The warehouse has no data yet - `run_load.py` hasn't run successfully. Run the pipeline manually (extract → cleanse → load, see The data pipeline) or trigger the DAG from the Airflow UI, then check `docker compose logs airflow-worker` for the `load` task if it's still empty after that.

**`/api/genres/trending` returns an empty list**
Expected, not a bug, until the pipeline has run on two different calendar days - see the Warehouse section above.

**Docker Desktop won't start on Windows**
Enable virtualization in BIOS and enable "Windows Subsystem for Linux" + "Virtual Machine Platform" Windows features. Reboot.

**Skip generating `__pycache__` folders**
Set once in your shell profile:
- Mac/Linux (`~/.zshrc` or `~/.bashrc`): `export PYTHONDONTWRITEBYTECODE=1`
- Windows PowerShell: `[Environment]::SetEnvironmentVariable("PYTHONDONTWRITEBYTECODE", "1", "User")`

---

## What runs where — architecture

```
┌───────────┐  schedules & runs   ┌──────────────────┐
│  Airflow  │────────────────────>│ app/services/     │
│  (batch)  │                     │  extractors,      │
└───────────┘                     │  cleansing        │
                                   └─────────┬─────────┘
                                             │ writes
                                             ▼
                                   ┌──────────────────┐
                                   │  data/raw,        │
                                   │  data/processed   │
                                   └─────────┬─────────┘
                                             │ reads (load stage)
                                             ▼
                                   ┌──────────────────┐
                                   │  Postgres         │
                                   │  (app-postgres)   │
                                   └─────────┬─────────┘
                                             │ reads
                                             ▼
┌───────────┐   HTTP requests     ┌──────────────────┐
│  React    │────────────────────>│  FastAPI          │
│ frontend  │                     │  (interactive)    │
└───────────┘                     └──────────────────┘
```

- **Airflow** produces data on a schedule (or on-demand via `npm run start`), ending with a load into Postgres. No user talks to Airflow directly.
- **FastAPI** reads from Postgres, not from `data/processed/` directly — it does no normalization itself, and its read path has no idea flat JSON files exist upstream.
- Both `app/services/extractors` and `app/services/cleansing` are pure functions with no HTTP or file I/O baked into their core logic — that's what makes them equally usable from Airflow tasks and from tests without a running pipeline.
