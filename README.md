# World Genre

Full-stack data project: scrape music chart data per country, cleanse it, serve it to a React frontend.

- **Frontend**: Vite + React (TypeScript)
- **Backend API**: FastAPI (serves data to the frontend)
- **Batch pipelines**: Airflow (extracts, cleanses, loads on a schedule)
- **Shared code**: `backend/app/services/` — imported by both FastAPI and Airflow

## Repo layout

```
world-genre/
├── frontend/                    # Vite React app
└── backend/
    ├── app/
    │   ├── main.py              # FastAPI entrypoint
    │   ├── api/routes/          # HTTP endpoints
    │   ├── schemas/             # Pydantic models
    │   ├── services/            # business logic (imported by API + Airflow)
    │   │   └── extractors/      # one module per data source
    │   └── core/                # config, seed loaders
    ├── seeds/                   # static reference data (countries.csv)
    ├── scripts/                 # runnable entrypoints (dev / debug)
    ├── dags/                    # Airflow DAGs
    ├── data/                    # gitignored: raw + processed output
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

> Windows users: install [WSL 2](https://learn.microsoft.com/en-us/windows/wsl/install) and run all commands below inside Ubuntu. Everything works identically to Mac/Linux from there. The alternative (PowerShell + native Windows Python) works but venv activation and path separators differ; instructions for both are below.

---

## First-time setup

```bash
git clone <repo-url> world-genre
cd world-genre
```

### Frontend

```bash
cd frontend
npm install
```

### Backend

```bash
cd backend
cp .env.example .env      # then fill in the SPOTIFY_* and LASTFM_API_KEY values
```

**Mac / Linux / WSL:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows PowerShell (no WSL):**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If PowerShell blocks the activate script, run once as admin: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

---

## Running in development

Open three terminals. Each hosts one long-running process.

### Terminal 1 — Frontend

```bash
cd frontend
npm run dev
```

Opens at `http://localhost:5173`. Vite proxies `/api/*` to the backend (see `frontend/vite.config.ts`).

### Terminal 2 — Backend API

```bash
cd backend
source .venv/bin/activate            # Windows: .\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```

- API: `http://localhost:8000`
- Auto-generated docs: `http://localhost:8000/docs`
- Health check: `curl http://localhost:8000/api/health`

### Terminal 3 — Airflow (Docker)

Ensure Docker Desktop is running (whale icon in the menu bar / system tray).

```bash
cd backend

# One-time: init the Airflow metadata DB + create admin user
docker compose up airflow-init

# Start scheduler, webserver, workers
docker compose up -d
```

- Airflow UI: `http://localhost:8080`
- Default login: `airflow` / `airflow`
- Container status: `docker compose ps`
- Logs: `docker compose logs -f`
- Stop everything: `docker compose down`
- Wipe state: `docker compose down -v`

The compose file mounts `./app`, `./seeds`, `./dags`, and `./data` into the containers so DAGs can `from app.services.extractors import ...` without a rebuild.

---

## Running the extractors manually (without Airflow)

Useful for debugging a single source before wiring it into a DAG. From `backend/` with venv activated:

```bash
python -m scripts.run_extract_kworb
python -m scripts.run_extract_spotify
python -m scripts.run_extract_lastfm
```

Output lands in `backend/data/raw/<source>/`.

---

## Environment variables

All secrets live in `backend/.env` (gitignored). Template in `.env.example`.

| Variable                | Used by       | Where to get it                                                    |
| ----------------------- | ------------- | ------------------------------------------------------------------ |
| `SPOTIFY_CLIENT_ID`     | Spotify extractor | [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) |
| `SPOTIFY_CLIENT_SECRET` | Spotify extractor | same as above                                                  |
| `LASTFM_API_KEY`        | Last.fm extractor | [last.fm/api/account/create](https://www.last.fm/api/account/create) |
| `API_HOST`, `API_PORT`  | FastAPI       | defaults are fine                                                  |

For Airflow, prefer setting secrets in the Airflow UI (Admin → Variables) over `.env`.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'app'` when running scripts**
Run scripts as modules from `backend/`: `python -m scripts.run_extract_kworb`, not `python scripts/run_extract_kworb.py`.

**Backend returns 404 on `/api/health`**
Uvicorn didn't reload after an edit. Save the file again, or restart it.

**Airflow containers exit immediately**
Run `docker compose logs airflow-init` — usually a permissions issue on Linux. Ensure `AIRFLOW_UID` is set in `.env`: `echo "AIRFLOW_UID=$(id -u)" >> .env`.

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
                                        │  data/ or DB     │
                                        └────────┬─────────┘
                                                 │ reads
                                                 ▼
┌───────────┐    HTTP requests          ┌──────────────────┐
│  React    │──────────────────────────>│  FastAPI         │
│ frontend  │                           │  (interactive)   │
└───────────┘                           └──────────────────┘
```

- **Airflow** produces data on a schedule. No user talks to it directly.
- **FastAPI** serves already-produced data to the frontend on demand.
- Both import the same `app/services/` package — that's why extractors are pure functions with no HTTP or file I/O baked in.
