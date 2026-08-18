# World Genre

**What does each country actually listen to — and what makes its taste
different from everywhere else?**

A data pipeline that collects music charts from 76 countries, reconciles them
against three metadata sources, and serves the result as an interactive map.

**Live:** https://world-genre-api-411464225527.asia-southeast3.run.app

Running on Google Cloud for roughly **$0.10/month**.

---

## The interesting part

Ranking genres by popularity makes every country look identical. Pop and rock
lead almost everywhere, so a "top genres" chart says more about the global
music industry than about any particular country.

So the pipeline computes a second score — **distinctiveness** — that weights
each genre by how few other countries listen to it:

```
distinctiveness = score × ln(total_countries / countries_with_genre)
```

A genre present everywhere scores `ln(1) = 0` and drops out entirely. What
remains is what actually sets a country apart. The UI lets you toggle between
the two, and the difference is the point of the project.

The same idea applied to artists gives **hidden gems** — acts who chart
strongly in one country and almost nowhere else.

---

## Architecture

```
   kworb ─┐
 Last.fm ─┤                                            ┌─ BigQuery ─┐
MusicBrainz ─┼─→ GCS (data lake) ─→ cleanse ─→ load ─→ │  warehouse │
  Deezer ─┤                                            └──────┬─────┘
 Wikidata ─┘                                                  │
                                                          publish
                                                              │
                                                              ▼
                                              GCS (JSON) ─→ Cloud Run ─→ browser
```

| Layer | Service | Why |
| --- | --- | --- |
| Data lake | Cloud Storage | Raw extractor output, cached per artist |
| Warehouse | BigQuery | Partitioned fact table, analytical SQL |
| Serving mart | Cloud Storage | Pre-rendered API payloads as JSON |
| App | Cloud Run | FastAPI + the React SPA, one service |

**The API never queries BigQuery.** Every read runs once at pipeline time and
the results are written as static JSON. BigQuery answers in 0.5–2s regardless
of table size — it has no point lookups — so a country page needing six
queries would take seconds and bill scan quota on every click. Publishing
turns that into a ~20ms file read.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the reasoning in depth,
[DEPLOYMENT.md](DEPLOYMENT.md) for the infrastructure.

---

## Data sources

| Source | Provides | Scope | Constraint |
| --- | --- | --- | --- |
| kworb | Spotify chart positions and streams | per country | scraped; no API |
| Last.fm | top artists, genre tags, listeners | per country | API key, ~5 req/s |
| MusicBrainz | genre tags, artist origin | global | **~1 req/s** |
| Deezer | artist images, fan counts | global | no auth |
| Wikidata | artist images, origin, formation year | global | batched SPARQL |

No single source is sufficient. kworb has measured streams but no genres.
Last.fm has genres but its own audience rather than Spotify's. MusicBrainz has
the best metadata and a rate limit that makes it unusable as a primary path —
resolving every artist through it alone took **106 minutes**, which is why
Wikidata's batched SPARQL does the bulk of that work now.

---

## Running the pipeline

Requires the Google Cloud CLI, authenticated, and Python 3.11+.

```powershell
cd backend
..\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-cloud.txt

$env:BQ_DATASET  = "world-genre-natt.world_genre"
$env:DATA_DIR    = "gs://world_genre_bucket/data"
$env:PUBLISH_DIR = "gs://world-genre-serving/published"
```

Then, in order:

```powershell
..\.venv\Scripts\python.exe -m scripts.run_init_bq          # create tables (idempotent)

..\.venv\Scripts\python.exe -m scripts.run_extract_kworb    # ~4 min
..\.venv\Scripts\python.exe -m scripts.run_extract_lastfm   # ~2 min
..\.venv\Scripts\python.exe -m scripts.run_extract_musicbrainz  # ~33 min cold
..\.venv\Scripts\python.exe -m scripts.run_extract_deezer   # ~2 min
..\.venv\Scripts\python.exe -m scripts.run_extract_wikidata # <1 min

..\.venv\Scripts\python.exe -m scripts.run_cleanse          # local, seconds
..\.venv\Scripts\python.exe -m scripts.run_load             # → BigQuery

..\.venv\Scripts\python.exe -m scripts.run_extract_artist_meta   # origins
..\.venv\Scripts\python.exe -m scripts.run_extract_genre_info    # descriptions

..\.venv\Scripts\python.exe -m scripts.run_publish          # → JSON, ~1 min
```

**A cold run takes about an hour; a warm one about 15 minutes.** MusicBrainz
is the whole difference — its per-artist results are cached in the bucket, so
repeat runs only pay for artists that newly charted.

`backend/dags/genre_pipeline_dag.py` expresses the same stages with their real
dependencies for Airflow (`npm run airflow:up`, localhost:8080). It is not
deployed: Cloud Composer bills ~$400/month for an always-on cluster, which is
40× the cost of everything else here combined.

### Publish only

Data already in BigQuery, just rebuilding the JSON:

```powershell
npm run publish
```

---

## Deploying

```powershell
npm --prefix frontend run build

gcloud builds submit --config cloudbuild.api.yaml `
  --substitutions=_IMAGE=asia-southeast3-docker.pkg.dev/world-genre-natt/world-genre/api

gcloud run deploy world-genre-api `
  --image asia-southeast3-docker.pkg.dev/world-genre-natt/world-genre/api `
  --region asia-southeast3 --allow-unauthenticated `
  --set-env-vars "PUBLISH_DIR=gs://world-genre-serving/published"
```

The build context is the **repo root**, not `backend/` — the image needs both
`backend/app` and `frontend/dist`, and Docker cannot copy from outside its
context.

**Two environment variables are the entire runtime configuration.** The API
reads published JSON and holds no connections, so it needs no dataset, no
credentials beyond its own service account, and no database URL. Serving the
SPA from the same service makes it same-origin, which removes CORS entirely.

---

## Local development

```powershell
npm run offline
```

Serves the API and Vite dev server against `backend/data/published`. No
Docker, no database, no cloud credentials. Run `npm run publish` first to
refresh that directory from BigQuery.

---

## Endpoints

| Endpoint | Returns |
| --- | --- |
| `GET /api/health` | readiness — 503 until a publish has landed |
| `GET /api/countries` | every country's summary |
| `GET /api/countries/{code}` | full detail, genre panels included |
| `GET /api/countries/{code}/genres/{genre}` | one genre's description and artists |
| `GET /api/artists/global` | worldwide artist ranking |
| `GET /api/genres/trending` | biggest genre movements |

`/api/health` is a real readiness check: it confirms the published data is
readable, not merely that the process is up. A container that starts but can't
reach its bucket reports `degraded`, which a plain 200 would hide.

---

## Views

**Map** — choropleth by top genre or domestic share, zoom and pan, click to
fit. A hand-rolled equirectangular projection over GeoJSON, no mapping library.

**Grid** — every country as a card with cover art.

**Trends** — genres rising and falling, via `LAG` over consecutive snapshots.

**Global artists** — worldwide ranking by summed streams.

**Compare** — two countries side by side. Overlap is histogram intersection:
for each genre, the smaller of the two shares, summed. If both spend 20% on
pop that's 20 points of real overlap; if one spends 30% and the other 5%, only
5 points are shared.

---

## Testing

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest      # 301 tests
..\.venv\Scripts\python.exe -m ruff check .
```

Two things worth knowing about how this suite is built.

**BigQuery SQL is validated offline.** There is no local BigQuery emulator, so
every query is parsed in the BigQuery dialect and checked against nine SQL constructs
BigQuery rejects, by name. That catches dialect errors in CI rather
than in a scheduled job at 6am — `FILTER (WHERE ...)` parses fine and is
rejected by BigQuery at runtime, so parsing alone is not enough.

**The API is tested against published payloads**, including the state that
happens on every first deploy: nothing published yet must return a 503 naming
the publish step, not a 500 or a misleading 404.

The recurring lesson across this project's real bugs is that **the expensive
failures were silent** — an empty API response returning 200, integer division
truncating a score to zero, a threshold quietly changing meaning at a
different sample depth. Tests here lean toward asserting data is *plausible*,
not merely present.

---

## Known limitations

Measured, not hypothetical.

**Deezer matches artists by name and gets it wrong about 29% of the time.**
Its search returned a name we never asked for in 67 of 754 lookups
(`Young Thug` → `Young T.H.U.G.`, 323 fans), and 219 artists came back under
100 fans — impossible for anyone in a national top 200. The matcher now
requires an exact accent-insensitive match and takes the highest fan count
among ties, storing nothing when it can't be confident. **The fix is in the
code but not yet in the data** — that needs a re-run of `run_extract_deezer`.

**Last.fm covers a median 26% of chart artists, and that is not a gap.**
`geo.getTopArtists` ranks by scrobbles — Radiohead is its top US artist. The
two sources disagree because their populations disagree, which is exactly why
the UI shows Spotify streams, Last.fm listeners and Deezer fans side by side
and never sums them.

**Artist origins fill in gradually.** MusicBrainz's rate limit is real, so
domestic-share coverage climbs across runs rather than arriving complete. The
UI reports its own coverage percentage, because the figure is uninterpretable
without it.

**Some countries have thin data.** Andorra has no kworb chart at all; Cyprus
and Luxembourg have partial ones. Those cards render sparse by design.

---

## Cost

| Service | Monthly |
| --- | --- |
| Artifact Registry (container images) | ~$0.10 |
| Cloud Storage (~16 MB across two buckets) | <$0.01 |
| BigQuery | $0 — under the 10 GiB / 1 TiB free tier |
| Cloud Run | $0 — scales to zero |
| Cloud Build | $0 — under 2,500 free minutes |

Note the free tiers for Cloud Storage and Artifact Registry are **US-region
only**, so those two bill from the first byte in `asia-southeast3`. Container
image versions are the only line item that grows without bound; a cleanup
policy (`cleanup.json`) keeps the three most recent and deletes untagged
versions after 7 days.
