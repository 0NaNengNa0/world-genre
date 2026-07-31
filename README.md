# World Genre

Full-stack data project: scrape music chart data per country, cleanse and reconcile it, serve it to a React frontend.

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
    │   │   └── countries.py     # reads already-cleansed data for the API
    │   └── core/                # config, seed loaders
    ├── seeds/                   # static reference data
    │   ├── countries.csv        # lastfm_name (API param) vs country_name (display)
    │   ├── genre_buckets.txt    # curated <200-genre taxonomy
    │   └── musicbrainz_genres.txt  # ~2,200-genre canonical list (fetched, not hand-written)
    ├── scripts/                 # runnable entrypoints (dev / debug / Airflow tasks)
    ├── dags/
    │   └── genre_pipeline_dag.py
    ├── data/                    # gitignored: raw/ (per source) + processed/ (cleansed)
    ├── docker-compose.yaml      # Airflow stack
    ├── .env.example
    └── requirements.txt
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

### One-time: canonical genre list + Airflow init

```bash
cd backend
.venv-relative-python -m scripts.fetch_musicbrainz_genres   # e.g. ..\.venv\Scripts\python -m scripts.fetch_musicbrainz_genres
docker compose up airflow-init      # one-time: init the Airflow metadata DB + admin user
cd ..
```

The genre list is a reference table (like a small dataset download), not something the recurring pipeline re-fetches — rerun it occasionally to pick up genres MusicBrainz has added, not on every pipeline run.

---

## Running in development

### One command (recommended)

```bash
npm run start
```

Brings up Airflow, triggers the `genre_pipeline` DAG once (retrying for up to a minute while Airflow finishes starting up), and runs the frontend + backend together — all under one process tree, labeled and colored per source in a single terminal. `Ctrl+C` once stops all of it, Airflow included.

- Frontend: `http://localhost:5173`
- Backend API: `http://localhost:8000` (docs at `/docs`, health at `/api/health`)
- Airflow UI: `http://localhost:8080` (login `airflow` / `airflow`)

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

Five sources feed into one cleansing step:

| Step | Script | Reads | Writes |
| --- | --- | --- | --- |
| Extract | `scripts/run_extract_kworb.py` | kworb.net (scrape) | `data/raw/kworb/{code}.json` |
| Extract | `scripts/run_extract_lastfm.py` | Last.fm API | `data/raw/lastfm/{code}.json` |
| Extract | `scripts/run_extract_musicbrainz.py` | MusicBrainz API | `data/raw/musicbrainz/{code}.json` |
| Extract | `scripts/run_extract_deezer.py` | Deezer API (images only) | `data/raw/deezer/artists.json` |
| Cleanse | `scripts/run_cleanse.py` | all of the above | `data/processed/{code}.json` |

The cleanse step (`app/services/cleansing.py`) does three things the raw extractors deliberately don't:
1. **Normalizes genre spelling** — "hiphop"/"hip hop"/"rap" all resolve to one form, fuzzy-matched against MusicBrainz's canonical ~2,200-genre list (`seeds/musicbrainz_genres.txt`).
2. **Buckets onto a coarser taxonomy** — `app/services/genre_buckets.py` collapses that onto `seeds/genre_buckets.txt` (<200 broad genres), so "chicago drill" and "trap" both count toward something meaningful to compare across 20 countries, instead of staying too fine-grained to ever overlap.
3. **Reconciles Last.fm + MusicBrainz** — both sources vote into the same bucket rather than one silently falling back to the other; each result's `sources` field shows whether Last.fm, MusicBrainz, or both agreed.

Spotify was dropped as a source entirely (it stopped exposing chart/genre data to third-party dev-mode apps in Feb 2026) — `app/services/extractors/spotify.py` and `scripts/run_extract_spotify.py` are unused, safe to delete.

### Running it manually (without Airflow)

```bash
python -m scripts.run_extract_kworb
python -m scripts.run_extract_lastfm
python -m scripts.run_extract_musicbrainz
python -m scripts.run_extract_deezer
python -m scripts.run_cleanse
```

### DAG (`dags/genre_pipeline_dag.py`)

```
kworb ──┬──> musicbrainz ──┐
lastfm ─┘                  ├──> cleanse
kworb ──┬──> deezer ────────┘
lastfm ─┘
```
`kworb` and `lastfm` run in parallel with no dependencies; `musicbrainz` and `deezer` each need both (Last.fm for artist MBIDs/names, kworb as fallback); `cleanse` waits on `kworb`, `lastfm`, and `musicbrainz` (not `deezer`, which only feeds cover images, read directly by `app/services/countries.py`).

---

## Environment variables

All secrets live in `backend/.env` (gitignored). Template in `backend/.env.example`.

| Variable                     | Used by            | Where to get it |
| ----------------------------- | ------------------- | ---------------- |
| `LASTFM_API_KEY`              | Last.fm extractor   | [last.fm/api/account/create](https://www.last.fm/api/account/create) |
| `API_HOST`, `API_PORT`        | FastAPI             | defaults are fine |
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

**Docker Desktop won't start on Windows**
Enable virtualization in BIOS and enable "Windows Subsystem for Linux" + "Virtual Machine Platform" Windows features. Reboot.

**Skip generating `__pycache__` folders**
Set once in your shell profile:
- Mac/Linux (`~/.zshrc` or `~/.bashrc`): `export PYTHONDONTWRITEBYTECODE=1`
- Windows PowerShell: `[Environment]::SetEnvironmentVariable("PYTHONDONTWRITEBYTECODE", "1", "User")`

---

## What runs where — architecture

```
┌───────────┐     schedules & runs      ┌──────────────────┐
│  Airflow  │──────────────────────────>│ app/services/    │
│  (batch)  │                           │  extractors,     │
└───────────┘                           │  cleansing       │
                                        └────────┬─────────┘
                                                 │ writes
                                                 ▼
                                        ┌──────────────────┐
                                        │  data/raw,       │
                                        │  data/processed  │
                                        └────────┬─────────┘
                                                 │ reads
                                                 ▼
┌───────────┐    HTTP requests          ┌──────────────────┐
│  React    │──────────────────────────>│  FastAPI         │
│ frontend  │                           │  (interactive)   │
└───────────┘                           └──────────────────┘
```

- **Airflow** produces data on a schedule (or on-demand via `npm run start`). No user talks to it directly.
- **FastAPI** serves already-cleansed data (`data/processed/`) to the frontend on demand — it does no normalization itself.
- Both import the same `app/services/` package — that's why extractors and cleansing logic are pure functions with no HTTP or file I/O baked into their core logic.
