# World Genre

Full-stack data project: scrape music chart data per country, cleanse and reconcile it, serve it to a React frontend.

The UI is a grid of country cards; clicking one opens a detail view with the country's full ranked artist list and its genre split as a donut chart — toggleable between **real stats** (share of what's actually played) and **TF-IDF** (share of what makes that country different from the rest).

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
npm run stop    # docker compose down + force-frees ports 5173/8000 — use if Ctrl+C ever leaves something orphaned
```

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
| Cleanse | `scripts/run_cleanse.py` | all of the above | `data/processed/{code}.json` |
| Load | `scripts/run_load.py` | `data/processed/{code}.json` | Postgres (`countries`, `country_snapshots`, `country_genre_scores`, `country_top_artists`) |

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
python -m scripts.run_cleanse
python -m scripts.run_load       # needs app-postgres up and run_init_db already applied
```

### DAG (`dags/genre_pipeline_dag.py`)

```
kworb ──┬──> musicbrainz ──┐
lastfm ─┘                  ├──> cleanse ──> load
kworb ──┬──> deezer ────────┘
lastfm ─┘
```
`kworb` and `lastfm` run in parallel with no dependencies; `musicbrainz` and `deezer` each need both (Last.fm for artist MBIDs/names, kworb as fallback); `cleanse` waits on `kworb`, `lastfm`, and `musicbrainz` (not `deezer`, which only feeds cover images, read directly by `app/services/countries.py`); `load` waits only on `cleanse`.

---

## Warehouse (Postgres)

`scripts/run_load.py` is the pipeline's "load" stage - it upserts each day's cleansed output into Postgres, and the FastAPI backend reads from there (`app/services/countries.py`), not from `data/processed/*.json` directly. Schema (`backend/sql/schema.sql`):

| Table | Grain | Notes |
| --- | --- | --- |
| `countries` | one row per country | code, display name |
| `country_snapshots` | one row per (country, day) | `artist_count` |
| `country_genre_scores` | one row per (country, genre, day) | `score` (popularity), `distinctiveness` (IDF), `sources` (array) - this is the time series |
| `country_top_artists` | one row per (country, day, rank) | top 5 artists that day |

Every load is an upsert keyed on `(..., snapshot_date)`, so rerunning the pipeline the same day overwrites that day's numbers instead of duplicating rows.

Genres are stored in **full** (every genre a country has, ~30–60 of them), not just the handful any one view shows. Two reasons: percentage share is only correct when divided by the true total, and "top 5 by popularity" vs "top 10 by distinctiveness" then differ by an `ORDER BY`/`LIMIT` instead of being baked in at write time — where changing a view would mean re-running an hour-long pipeline.

Three queries live in `backend/sql/queries/`, runnable directly with `psql "$DATABASE_URL" -f sql/queries/<file>.sql` or through Python:

- **`country_genre_shares.sql`** — each genre's share of a country's total genre weight, via `SUM(score) OVER ()` computed *before* the `LIMIT` so the slices plus the leftover "other" total 100. Backs the detail view's pie chart at `GET /api/countries/{code}`.
- **`trending_genres.sql`** — genre score deltas between each country's two most recent load dates, using `LAG()` over a window. Wired up at `GET /api/genres/trending`. Needs at least two days of pipeline history to return anything — with a fresh database it legitimately returns `[]`, not a bug.
- **`similar_countries.sql`** — which country pairs share the most top genres, via a self-join. Not wired to an endpoint; kept as a documented ad hoc analytical query, which is a normal part of the job too, not everything needs a route.

> Gotcha worth knowing if you edit these: psycopg2 scans the whole `.sql` file for parameter placeholders **without stripping SQL comments**, so a bare `%` character even inside a `--` comment raises `argument formats can't be mixed`. Spell the word out instead.

### Query design

`GET /api/countries` renders 76 cards, and the obvious implementation — loop the countries, fetch each one's genres and artists — issued **305 queries per request**, each re-deriving that country's latest `snapshot_date` with a correlated subquery. It now uses `ROW_NUMBER() OVER (PARTITION BY country_code ...)` to rank every country's genres in one pass, so the endpoint is **3 queries regardless of how many countries exist**: 66 ms → 19 ms locally, and a much bigger gap against a networked database where per-round-trip latency dominates rather than query time.

The same endpoint also used to return all 100 artists per country — about 7,600 strings for a grid that displays three each. It now returns 5, scanning 20 server-side to pick a cover image (roughly 7% of artists have no Deezer photo, so taking only the first would show a placeholder whenever artist #1 is one of them).

---

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

`.github/workflows/backend-ci.yml` runs lint then the full test suite (DB tests included) on every push/PR that touches `backend/`, same commands as above.

---

## Environment variables

All secrets live in `backend/.env` (gitignored). Template in `backend/.env.example`.

| Variable                     | Used by            | Where to get it |
| ----------------------------- | ------------------- | ---------------- |
| `LASTFM_API_KEY`              | Last.fm extractor   | [last.fm/api/account/create](https://www.last.fm/api/account/create) |
| `API_HOST`, `API_PORT`        | FastAPI             | defaults are fine |
| `DATABASE_URL`                 | FastAPI, `run_load.py`, `run_init_db.py` | defaults to `localhost:5433` (the `app-postgres` service's host-mapped port) — only change this if you're pointing at a different Postgres. Airflow containers override this automatically to reach the same database by service name; see `docker-compose.yaml`. |
| `_PIP_ADDITIONAL_REQUIREMENTS`| Airflow containers   | dev-only — installs `requests`/`beautifulsoup4`/`lxml`/`python-dotenv` into the Airflow image at startup, since the extractors need them and the stock Airflow image doesn't have them. For anything long-lived, bake these into a custom image instead. |

MusicBrainz and Deezer need no key at all. For Airflow specifically, prefer setting secrets in the Airflow UI (Admin → Variables) over `.env` if this ever goes beyond local dev.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'app'` when running scripts**
Run scripts as modules from `backend/`: `python -m scripts.run_extract_kworb`, not `python scripts/run_extract_kworb.py`.

**Backend returns 404 on `/api/health`**
Uvicorn didn't reload after an edit. Save the file again, or restart it.

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
