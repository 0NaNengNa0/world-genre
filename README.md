# World Genre

**What does each country actually listen to — and what makes its taste
different from everywhere else?**

A production data pipeline that collects music charts from 76 countries,
reconciles them against four metadata sources, and serves the result as an
interactive map.

**Live:** https://world-genre-api-411464225527.asia-southeast3.run.app

| | |
| --- | --- |
| **Runs** | Nightly on Cloud Run Jobs, 13 stages, ~25–35 min, unattended since August |
| **Volume** | ~7,300 chart rows/day across 76 countries; 4,356 artists in the dimension |
| **Warehouse** | BigQuery — 13 tables, partitioned facts, merge-upsert dimensions |
| **Quality** | 7-check gate between load and publish; a failing run cannot publish |
| **CI/CD** | ruff + 520 tests on every push; merge to main deploys via Workload Identity Federation |
| **Monitoring** | Two Cloud Monitoring policies — one for a run that failed, one for a run that never happened |
| **Cost** | ~$0.10/month |

Written in Python 3.11 (FastAPI, pandas-free), SQL, and React.

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
remains is what actually sets a country apart. The UI toggles between the two,
and the difference is the point of the project.

The same idea applied to artists gives **hidden gems** — acts who chart
strongly in one country and almost nowhere else.

---

## How it runs

```
Cloud Scheduler (daily 17:00 UTC)
        │
        ▼
Cloud Run Job ──► scripts/run_pipeline.py  (13 stages, digest-pinned image)
        │
        ├─ extract   kworb · Last.fm · MusicBrainz · Deezer · Wikidata  ──► GCS data lake
        ├─ cleanse   pure Python, no I/O ─────────────────────────────►  GCS
        ├─ load      replace-partition (facts) + merge-upsert (dims) ──►  BigQuery
        ├─ resolve   artist origin, joined against a MusicBrainz mirror
        ├─ enrich    the residue the join could not reach, via API
        ├─ validate  ◄── THE GATE. 7 checks. Fails here and publish is skipped.
        └─ publish   SQL results ──► static JSON ──► GCS
                                                      │
                                                      ▼
                                       FastAPI on Cloud Run ──► browser
```

**The API never queries BigQuery.** Every read runs once at pipeline time and
the result is written as static JSON. BigQuery answers in 0.5–2s regardless of
table size — it has no point lookups — so a country page needing six queries
would take seconds and bill scan quota on every click. Publishing turns that
into a ~20ms file read.

**A failed run degrades to stale, never to wrong.** The gate sits between load
and publish, so bad data can land in the warehouse without ever being served;
the API keeps serving the previous run's payloads. That ordering has a name —
**write-audit-publish** — and it is only possible because the warehouse isn't
the serving layer.

Run it yourself: `python -m scripts.run_pipeline`, or `--from load` to resume
after a failure, or `--only publish` for one stage. See
[DEPLOYMENT.md](DEPLOYMENT.md) for the deployed configuration and
[ARCHITECTURE.md](ARCHITECTURE.md) for the reasoning in depth.

---

## Decisions worth defending

- **Three write patterns, and picking the wrong one destroys data silently.**
  `replace_partition` for dated facts, `merge_dimension` for dimensions several
  scripts co-own, truncate-and-load for tables that mirror an external source.
  A dimension written by four scripts needs `update_columns` for what a writer
  owns and `fill_columns` (`COALESCE`) for what it may only seed — so Last.fm's
  free MusicBrainz id fills an empty field but never overwrites one the
  enrichment stage paid a rate-limited lookup for.

- **The resolver refuses to guess.** Artist origin resolves by known MBID, then
  by unambiguous name, then **not at all**. 242 chart names map to several real
  MusicBrainz artists; the tempting tie-break — "pick the one whose country
  matches where they charted" — would manufacture the domestic-share number the
  whole product reports. Unresolved artists stay NULL and the UI reports its own
  coverage.

- **Images are pinned by digest, never `:latest`.** A tag is a mutable pointer;
  a digest is the image. Deploying resolves the tag once and pins the digest.

- **Schema is declared, never inferred.** Load jobs run with
  `create_disposition=CREATE_NEVER` and staging tables borrow the target's
  schema, because a load job will otherwise invent a table — or infer a column
  of all-nulls as STRING and break a MERGE that worked yesterday.

- **Airflow is free; Cloud Composer is ~$400/month.** The DAG in
  `backend/dags/` expresses the real dependency graph and runs locally, but an
  orchestrator's overhead is justified by the number of interdependent
  pipelines, not by the complexity of one. Cloud Scheduler plus a job that
  knows its own stage order costs nothing.

---

## Failures worth reading

The debugging is the part of this project I'd most want reviewed.

**Production existed and the repo didn't know.** The pipeline had been running
nightly on Cloud Run since August while every document here said it was run by
hand. The driver it executed lived only inside a container image — not in git —
and was reconstructed from the job's own Cloud Logging output into
`scripts/run_pipeline.py`. A laptop build had also overwritten production's
`:latest` tag; that is why everything is digest-pinned now.

**An N+1 API pattern that could never converge.** Artist enrichment made one
rate-limited HTTP call per artist and resolved ~50 a night against a backlog
growing about as fast. Measuring first predicted ~546 resolvable by a bulk join
against a MusicBrainz dump. The first run delivered 255 — *below* a deliberately
conservative bound, which meant the bound wasn't the problem. Two queries killed
the obvious hypothesis; the third found it: 57% of the backlog had no join key,
because the key was computed only for artists on that day's chart. Backfilling
the whole dimension brought it to 542 against a prediction of 546. Backlog
1,602 → 1,020, zero API requests.

**A batch of nulls became a type error.** `merge_dimension` loaded its staging
table with `autodetect=True`. BigQuery infers an all-NULL column as STRING, so
the first night MusicBrainz returned no formation year for anyone in the batch,
the staging column came out STRING and the MERGE into INT64 failed. Nothing in
the code had changed — the data had. Schema inference over a sample makes a
table's type a function of the rows that happened to arrive, and the unlucky
batch arrives eventually.

**Three data-quality checks were wrong on first contact with real data.** One
divided by the wrong denominator and read 0.157 as coverage. One was
structurally incapable of failing. One passed vacuously on an empty set —
which is how the gate gained a `skip` status distinct from `pass`, because an
assertion over nothing is abstaining, not passing.

---

## Data sources

| Source | Provides | Scope | Constraint |
| --- | --- | --- | --- |
| kworb | Spotify chart positions and streams | per country | scraped; no API |
| Last.fm | top artists, genre tags, listeners, MBIDs | per country | API key, ~5 req/s |
| MusicBrainz | artist origin, formation year, genre tags | global | **~1 req/s**; bulk dump mirrored into BigQuery |
| Deezer | artist images, fan counts | global | no auth; name matching is unreliable |
| Wikidata | artist images, origin, formation year | global | batched SPARQL |

No single source is sufficient. kworb has measured streams but no genres.
Last.fm has genres but its own audience rather than Spotify's. MusicBrainz has
the best metadata and a rate limit that makes it unusable as a primary path —
which is why 3.5M artist names now live in BigQuery as a joinable mirror.

---

## Testing

```powershell
cd backend
python -m pytest        # ~520 tests, 3 seconds, no credentials
ruff check .
```

Three layers, and the boundaries between them are deliberate.

**Static analysis** (ruff: pyflakes, pycodestyle, import order) catches what is
wrong with the code.

**Stubbed unit tests** catch what is wrong with the logic. Nothing touches
BigQuery — there is no emulator — so pure functions are tested directly and the
client is stubbed, with assertions on the *generated SQL* and the *job config*
rather than on results. Every `.sql` file is parsed in the BigQuery dialect and
checked against nine Postgres-only constructs by name, because `FILTER (WHERE
…)` parses cleanly and is rejected by BigQuery at runtime.

**Neither catches what is wrong with your assumptions about a system you are
not running** — which is how the all-NULL type error shipped green. The next
layer, still open, is a BigQuery dry run in CI: free, executes nothing, and
validates names, types and scoping against the real warehouse.

The recurring lesson across this project's real bugs is that **the expensive
failures were silent** — an empty API response returning 200, integer division
truncating a score to zero, a check that could not fail. Tests here lean toward
asserting data is *plausible*, not merely present.

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

## Known limitations

Measured, not hypothetical.

**Artist origin is partial, and the gap is honest.** 1,020 of 4,356 artists have
never been resolved: roughly 930 are absent from a 3.5M-row index of every name
MusicBrainz knows, and 87 are names shared by several real artists, which the
resolver refuses to disambiguate. Of those that were looked up, a further slice
came back with no country at all — MusicBrainz's coverage of smaller acts is
genuinely patchy. Every view that uses origin reports its own coverage
percentage, because the figure is uninterpretable without it.

**Last.fm covers a median 26% of chart artists, and that is not a gap.**
`geo.getTopArtists` ranks by scrobbles — Radiohead is its top US artist. The
two sources disagree because their populations disagree, which is exactly why
the UI shows Spotify streams, Last.fm listeners and Deezer fans side by side
and never sums them.

**Deezer matched artists by name and got it wrong about 29% of the time.** Its
search returned a name never asked for in 67 of 754 lookups (`Young Thug` →
`Young T.H.U.G.`, 323 fans). The matcher now requires an exact
accent-insensitive match and stores nothing when it cannot be confident.

**Some countries have thin data.** Andorra has no kworb chart at all; Cyprus
and Luxembourg have partial ones. Those cards render sparse by design.

---

## Local development

```powershell
npm run offline     # API + Vite against backend/data/published — no cloud, no Docker
npm run publish     # rebuild that directory from BigQuery
```

---

## Cost

| Service | Monthly |
| --- | --- |
| Artifact Registry (container images) | ~$0.10 |
| Cloud Storage (~16 MB across two buckets) | <$0.01 |
| BigQuery | $0 — under the 10 GiB / 1 TiB free tier |
| Cloud Run | $0 — scales to zero |
| Cloud Build | $0 — under 2,500 free minutes |

The free tiers for Cloud Storage and Artifact Registry are **US-region only**,
so both bill from the first byte in `asia-southeast3`. Container image versions
are the only line item that grows without bound; `cleanup.json` keeps the three
most recent and deletes untagged versions after 7 days.
