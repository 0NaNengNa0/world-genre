# Deployment

The infrastructure this runs on, and how to rebuild it from nothing.

Commands are PowerShell.

## What exists

| | |
| --- | --- |
| Project | `world-genre-natt` (number `411464225527`) |
| Region | `asia-southeast3` (Bangkok) |
| Data lake | `gs://world_genre_bucket/data` |
| Warehouse | `world-genre-natt.world_genre` (BigQuery) |
| Serving mart | `gs://world-genre-serving/published` |
| Image | `asia-southeast3-docker.pkg.dev/world-genre-natt/world-genre/api` |
| Service | `world-genre-api`, Cloud Run |

**Keep everything in one region.** Reads within a region are free; reads across
regions bill as egress on every request.

Bangkok opened in January 2026, so two caveats: not every service has reached
it, and newer regions price slightly above `us-central1`. Both are immaterial
at this scale but worth knowing before assuming a new service will be
available.

---

## Prerequisites

```powershell
gcloud init
gcloud auth login
gcloud auth application-default login
gcloud config set project world-genre-natt
```

Application Default Credentials rather than a service-account key file: the
Google clients find them automatically, there's nothing to leak into git, and
the same code picks up the service account identity on Cloud Run unchanged.

```powershell
gcloud services enable `
  storage.googleapis.com bigquery.googleapis.com run.googleapis.com `
  artifactregistry.googleapis.com cloudbuild.googleapis.com `
  compute.googleapis.com secretmanager.googleapis.com
```

Give enablement a minute to propagate. A command issued immediately after
`gcloud services enable` can fail with `PERMISSION_DENIED` naming *your*
account, which looks like a missing role even when you're Owner — it's the
service agent not yet existing. Retrying is the whole fix.

### Budget alert

Do this before anything else.

The Console is easier (Billing → Budgets & alerts) because it pre-fills your
currency. The CLI equivalent:

```powershell
gcloud services enable billingbudgets.googleapis.com

gcloud billing budgets create `
  --billing-account=BILLING_ACCOUNT_ID `
  --display-name="world-genre cap" `
  --budget-amount=10 `
  --threshold-rule=percent=0.5 `
  --threshold-rule=percent=0.9 `
  --threshold-rule=percent=1.0
```

Two things make this fail with a bare `INVALID_ARGUMENT`: the amount must be in
the billing account's **own currency** (`10USD` against a THB account is
rejected, and the error names neither field nor currency — omit the suffix to
use the account's), and `billingbudgets.googleapis.com` must be enabled
separately from the list above.

Budget alerts **notify, they do not stop spending.** A hard stop needs a Cloud
Function that unlinks billing.

---

## Storage

```powershell
gcloud storage buckets create gs://world_genre_bucket `
  --location=asia-southeast3 --uniform-bucket-level-access

gcloud storage buckets create gs://world-genre-serving `
  --location=asia-southeast3 --uniform-bucket-level-access
```

Uniform bucket-level access turns off per-object ACLs and makes IAM the single
source of truth. Without it you have two overlapping permission systems and no
clear answer to "who can read this".

Location and storage class **cannot be changed after creation.**

> The serving bucket uses hyphens deliberately. Underscores are legal in bucket
> names and fine for `gs://` access, but they aren't valid in a DNS hostname —
> so an underscore bucket can never back a virtual-hosted URL or a custom
> domain. The lake is only ever read through the client library; the serving
> bucket might not be.

Optional, to stop raw snapshots accumulating (history lives in the warehouse):

```powershell
'{"rule":[{"action":{"type":"Delete"},"condition":{"age":90}}]}' | Set-Content lifecycle.json
gcloud storage buckets update gs://world_genre_bucket --lifecycle-file=lifecycle.json
```

---

## Warehouse

```powershell
bq mk --location=asia-southeast3 --dataset world-genre-natt:world_genre

cd backend
$env:BQ_DATASET = "world-genre-natt.world_genre"
..\.venv\Scripts\python.exe -m scripts.run_init_bq
```

Eleven tables, all `CREATE TABLE IF NOT EXISTS`, so this is safe on every run.
Fact tables partition on `snapshot_date` and cluster on `country_code` —
partition pruning is what keeps reads cheap, since BigQuery bills on bytes
scanned.

Dataset names allow only letters, numbers and underscores. The format is
`project:dataset` — project on the left.

> `dq_runs` and `cleanse_quality` were added after the original nine. Re-run
> `scripts.run_init_bq` against an existing dataset to create them; nothing
> else is affected.

---

## Data-quality gate

The pipeline runs in this order, and `run_validate` is not optional:

```powershell
cd backend
$env:BQ_DATASET = "world-genre-natt.world_genre"
..\.venv\Scripts\python.exe -m scripts.run_cleanse
..\.venv\Scripts\python.exe -m scripts.run_load
..\.venv\Scripts\python.exe -m scripts.run_validate
..\.venv\Scripts\python.exe -m scripts.run_publish
```

`run_validate` exits non-zero when a blocking check fails, which is what stops
the chain before `run_publish`. There are two kinds of non-zero, and the
difference is what lets an orchestrator retry intelligently:

| Exit | Meaning | Retry? |
| --- | --- | --- |
| 0 | Every blocking check passed | — |
| 1 | A check ran and the **data** failed it | No — deterministic |
| 2 | A check **could not run** (403, network, outage) | Yes — transient |

A data failure outranks an execution error when both occur: retrying cannot fix
bad data, so reporting "transient" would send the orchestrator round a loop it
can never exit. A plain shell chain still stops on either, so this costs nothing
for a manual run. Because the API serves static JSON rather than
querying BigQuery, a publish that doesn't happen leaves the previous run's good
data in place — bad data can land in the warehouse without ever being served.
That ordering is the **write-audit-publish** pattern, and it is the reason the
gate sits between load and publish rather than at the end.

Six checks, defined in `backend/app/core/dq.py`, each one a query in
`backend/sql/bigquery/checks/` returning a single `value`. Every threshold on a
blocking check was set from four consecutive real partitions (2026-09-10 to
09-13), and the observed range is recorded in each check's description:

| Check | Asserts | Observed | Threshold |
| --- | --- | --- | --- |
| `chart_volume` | Rows loaded vs trailing 7-day median | 0.9997–1.001 | ≥ 0.95 (warn 0.98) |
| `countries_present` | Countries that charted this week and are missing today | 0 | = 0 |
| `chart_churn` | Chart slots changed since the previous snapshot | 0.820–0.873 | ≥ 0.30 (warn 0.60) |
| `duplicate_chart_positions` | Repeated `(country, position)` keys | 0 of 7,315 | = 0 |
| `artist_genre_coverage` | Tagged artists that got a genre | 0.948 | ≥ 0.85 (warn 0.92) |
| `unclassified_tag_rate` | Genre tags cleansing could not classify | none yet | ≤ 0.35, **advisory** |

`countries_present` exists because `chart_volume` provably cannot do its job:
one country out of 75 is ~1.3% of rows, inside the noise of a row-count ratio.
It compares against the trailing week rather than `seeds/countries.csv`,
because Andorra is in the seed list and has never charted — a seed-list
comparison would fail every run and be switched off within a week.

`artist_genre_coverage` divides by `country_artist_listeners`, **not**
`chart_entries`. Genre signal comes from the Last.fm per-country top list, and
`schema.sql` is explicit that Last.fm and the Spotify chart are different
populations. An earlier version divided by charting artists, read 0.157, and
blocked a publish over a regression that did not exist.

Five statuses, not three: `pass`, `warn`, `fail`, `error` (the check could not
execute — blocking, but a credentials problem rather than a data one) and
`skip` (nothing to measure). A check declares it may abstain by returning a
`sample_size` column; zero means skip. Exactly one check — `chart_volume` —
declares no `sample_size` and always renders a verdict, so an empty partition
produces one accurate failure and four honest abstentions rather than three
vacuous passes. That rule is enforced by a test.

Results append to `dq_runs` on every attempt, whatever the outcome — the
failures are the part worth keeping, and `MAX(run_ts)` there is the freshness
signal that `/api/health` cannot give (a healthy API serving month-old JSON
looks fine).

`unclassified_tag_rate` reads `cleanse_quality`, a table fed by `run_load` from
`data/processed/_quality_report.json`. It has to: `merge_genre_signals` counts
unclassified tags and then drops the `other` bucket **before** scoring, so
those tags are by construction absent from `country_genre_scores` and from
every other table in the warehouse. Until that report was loaded, the
pipeline's headline quality metric could not be asserted on at all.

It ships **advisory** (`blocking=False`) and should stay that way for a
fortnight. The ~18% quoted from `run_cleanse`'s report is an average of
per-country rates; this check is weighted by tag volume, so they are different
statistics and no observation of *this* number exists yet. It also could not be
backfilled — the report only ever kept "latest", which is the standing cost of
leaving a metric outside the warehouse. Promote it once `dq_runs` shows a real
range:

```powershell
$q = @'
SELECT MIN(value) AS lo, MAX(value) AS hi, COUNT(*) AS runs
FROM `world-genre-natt.world_genre.dq_runs`
WHERE check_name = 'unclassified_tag_rate' AND status != 'skip'
'@
bq query --use_legacy_sql=false ($q -replace "\r?\n", " ")
```

Note `bq` on Windows reads only the first line of a multi-line argument, hence
the `-replace`. Without it you get `Syntax error: Unexpected end of script`,
which points at the SQL rather than at the truncation.

Useful flags:

```powershell
..\.venv\Scripts\python.exe -m scripts.run_validate --date 2026-09-13
..\.venv\Scripts\python.exe -m scripts.run_validate --no-record
..\.venv\Scripts\python.exe -m scripts.run_validate --fail-on-warn
```

New checks should land with `blocking=False`, accumulate a few weeks of range
in `dq_runs`, and be promoted once their normal band is known. A threshold set
from one observation fires on ordinary variance, and a gate that cries wolf
gets bypassed within a week.

---

## Container image

Cloud Build runs as the **Compute Engine default service account**, which does
not carry the Cloud Build role by default in a new project. Without this grant
the build starts and then fails writing logs or pushing, which reads like a
build error rather than an IAM one:

```powershell
gcloud artifacts repositories create world-genre `
  --repository-format=docker --location=asia-southeast3

gcloud projects add-iam-policy-binding world-genre-natt `
  --member=serviceAccount:411464225527-compute@developer.gserviceaccount.com `
  --role=roles/cloudbuild.builds.builder
```

Build from the **repo root**:

```powershell
npm --prefix frontend run build

gcloud builds submit --config cloudbuild.api.yaml `
  --substitutions=_IMAGE=asia-southeast3-docker.pkg.dev/world-genre-natt/world-genre/api
```

The context is the root because the image needs both `backend/app` and
`frontend/dist`, and Docker cannot copy from outside its context. The frontend
is built beforehand rather than inside the image, so the image stays a single
Python layer with no Node toolchain.

> **`.gcloudignore` at the repo root is load-bearing.** Without it gcloud falls
> back to `.gitignore` — which excludes `dist/`, the one directory the image
> needs. The build would succeed, push an image with no frontend, and serve
> 404s at the root with nothing in the logs to explain it. It also keeps the
> context at ~1 MiB instead of 44 MiB; the larger archive is big enough to hit
> file-locking errors on Windows.

Check the first line of build output says roughly *60 files*. Thousands means
the ignore file isn't being read.

---

## Service

```powershell
gcloud run deploy world-genre-api `
  --image asia-southeast3-docker.pkg.dev/world-genre-natt/world-genre/api `
  --region asia-southeast3 `
  --allow-unauthenticated `
  --set-env-vars "PUBLISH_DIR=gs://world-genre-serving/published"

gcloud storage buckets add-iam-policy-binding gs://world-genre-serving `
  --member=serviceAccount:411464225527-compute@developer.gserviceaccount.com `
  --role=roles/storage.objectViewer
```

Without that IAM binding every request 403s.

**One environment variable is the whole runtime configuration.** The API reads
published JSON and holds no connections — no dataset, no database URL, no
credentials beyond its own identity. Serving the SPA from the same service
makes it same-origin, so there is no CORS setting to keep in sync either.

`$PORT` is assigned by Cloud Run and is not guaranteed to be 8080; the image
honours it. Binding a hardcoded port is the usual cause of a container that
builds, deploys, and then fails health checks.

### Verify

```powershell
curl.exe https://world-genre-api-411464225527.asia-southeast3.run.app/api/health
curl.exe -s -o NUL -w "%{http_code}" https://world-genre-api-411464225527.asia-southeast3.run.app/
```

Check them separately — it narrows a failure immediately:

| health | root | meaning |
| --- | --- | --- |
| `ok` | 200 | working |
| `ok` | 404 | frontend missing from the image |
| `degraded` | — | publish never ran, or wrote elsewhere |
| `ModuleNotFoundError` | — | stale image; rebuild |

`/api/health` returning `degraded` is the deploy working correctly, not
failing — it means the container is fine and the data isn't there.

---

## Image cleanup

Every build pushes a version and untags the previous one. Those orphans are
the only line item that grows without bound.

```powershell
gcloud artifacts repositories set-cleanup-policies world-genre `
  --location=asia-southeast3 --policy=cleanup.json
```

`cleanup.json` keeps the three most recent versions, deletes untagged ones
after 7 days, and deletes tagged ones after 30. `Keep` is evaluated before
`Delete`, so recent versions survive regardless. Add `--dry-run` to see what
would be removed before committing.

> The third rule exists because of CI. A manual `gcloud builds submit --tag`
> reuses one tag, so each build untags its predecessor and the 7-day untagged
> rule sweeps it up. The GitHub Actions deploy tags every image with its commit
> SHA instead — nothing is ever untagged, that rule stops matching, and storage
> grows forever. `Keep` rules protect, they do not delete, so `keep-recent-tagged`
> alone would not have caught it.

---

## Continuous deployment

`.github/workflows/deploy-api.yml` runs the sequence above on every push to
`main`: tests, frontend build, Cloud Build, `gcloud run deploy`, smoke test.
The manual commands still work and are still the reference for what each step
does — CI is not a second way of building, it calls the same
`cloudbuild.api.yaml`.

Two workflows, one test job. `backend-ci.yml` gained a `workflow_call` trigger
so `deploy-api.yml` can reuse it via `uses:`. The obvious alternative —
triggering the deploy on `workflow_run: [Backend CI]` — is broken by the path
filters: a frontend-only commit never runs Backend CI, so `workflow_run` never
fires and that commit silently never deploys. `backend-ci.yml`'s push trigger
now ignores `main`, since the deploy workflow already runs it there.

### Authentication

**Workload Identity Federation, not a service account key.** GitHub mints a
short-lived OIDC token asserting which repository the run came from; GCP is
configured to trust that issuer and exchanges it for a ~1-hour access token.
Nothing long-lived is stored in GitHub, so there is no key to rotate or leak.
The alternative — a JSON key in a repo secret — is a permanent private
credential in a place that gets forked, cloned and screenshotted.

Two APIs first. They are not in the enable list near the top of this file,
because nothing before CD needed them:

```powershell
gcloud services enable iamcredentials.googleapis.com sts.googleapis.com
```

The deploy identity and its roles:

```powershell
gcloud iam service-accounts create github-deployer `
  --display-name="GitHub Actions deployer"

$SA = "serviceAccount:github-deployer@world-genre-natt.iam.gserviceaccount.com"

foreach ($r in @(
  "roles/run.admin",                        # deploy revisions
  "roles/cloudbuild.builds.editor",         # submit builds
  "roles/artifactregistry.writer",          # push the image
  "roles/serviceusage.serviceUsageConsumer" # make API calls billed to this project
)) {
  gcloud projects add-iam-policy-binding world-genre-natt --member=$SA --role=$r
}

# Cloud Build uploads the source archive to this bucket before building.
# Bucket-scoped rather than project-wide: the identity needs this one bucket,
# not all of Cloud Storage.
gcloud storage buckets add-iam-policy-binding gs://world-genre-natt_cloudbuild `
  --member=$SA --role="roles/storage.admin"

# Cloud Run revisions run as the Compute default SA, and creating one means
# impersonating it. Without this the deploy fails at the last step with a
# permission error naming a service account you never referenced.
gcloud iam service-accounts add-iam-policy-binding `
  411464225527-compute@developer.gserviceaccount.com `
  --member=$SA --role=roles/iam.serviceAccountUser
```

The pool, the provider, and the binding that lets *this repository* use the
service account:

```powershell
gcloud iam workload-identity-pools create github --location=global

gcloud iam workload-identity-pools providers create-oidc github-provider `
  --location=global --workload-identity-pool=github `
  --issuer-uri="https://token.actions.githubusercontent.com" `
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" `
  --attribute-condition="assertion.repository=='0NaNengNa0/world-genre'"

gcloud iam service-accounts add-iam-policy-binding `
  github-deployer@world-genre-natt.iam.gserviceaccount.com `
  --role=roles/iam.workloadIdentityUser `
  --member="principalSet://iam.googleapis.com/projects/411464225527/locations/global/workloadIdentityPools/github/attribute.repository/0NaNengNa0/world-genre"
```

`--attribute-condition` is not optional — gcloud refuses to create a GitHub
provider without one, and correctly: the issuer is shared by every repository
on GitHub, so an unconditioned provider lets *anyone's* workflow assume this
service account. The condition is what narrows the trust to this repo.

Verify before merging anything. This must print the exact string that
`deploy-api.yml` carries as `workload_identity_provider`:

```powershell
gcloud iam workload-identity-pools providers describe github-provider `
  --location=global --workload-identity-pool=github --format="value(name)"
```

A mismatch fails at the token exchange, and the error points at the exchange
rather than at the mismatch.

The provider path and service account are written into `deploy-api.yml`
directly. Neither is a secret — the WIF grant is what authorizes, not knowledge
of the identifier — so there is no repo secret to configure at all.

### What the first three runs actually failed on

None of these were build failures. All three were the CI identity lacking a
permission that an Owner never notices they have, and each error named
something other than the missing role. Recorded because the pattern is the
point, not the specific roles.

| Error said | Actually missing | Fix |
| --- | --- | --- |
| `forbidden from accessing the bucket [world-genre-natt_cloudbuild]` … `serviceusage.services.use` | Both, in one message | `serviceusage.serviceUsageConsumer` on the project, `storage.admin` on the staging bucket |
| `can only stream logs if you are Viewer/Owner` | Read access to the **GCS** logs bucket | `options: logging: CLOUD_LOGGING_ONLY` in `cloudbuild.api.yaml` |
| Same again | — | `--suppress-logs` on `gcloud builds submit` |

The second one is worth understanding rather than memorising. Cloud Build's
default `LEGACY` logging writes to **two** destinations — Cloud Logging *and* a
Google-managed GCS bucket — and `gcloud builds submit` streams from the bucket.
So `roles/logging.viewer` looks like the obvious fix and does nothing: it grants
the wrong destination. Meanwhile the build itself was running and succeeding the
whole time; only gcloud's ability to narrate it had failed, and the non-zero
exit made a healthy build look broken. **Check the system's own record — Cloud
Build history — before believing a wrapper's exit code.**

`CLOUD_LOGGING_ONLY` removes the bucket destination entirely, so the permission
is no longer needed by anyone. `--suppress-logs` makes the step independent of
log access regardless of how the defaults shift later. Belt and braces, because
this failure mode costs a full deploy cycle to diagnose.

One loose end: `roles/storage.objectAdmin` was granted project-wide while
debugging the first error, and is probably redundant now that the staging bucket
carries its own binding. Confirm with the Policy Troubleshooter before removing
it — an unused grant on a CI identity is exactly the kind of thing that is
easier to remove now than to justify in a review later.

### Rolling back

Images are tagged with the commit SHA, so a revision is traceable to a commit
and the previous one is still there:

```powershell
gcloud run revisions list --service=world-genre-api --region=asia-southeast3

gcloud run services update-traffic world-genre-api `
  --region=asia-southeast3 --to-revisions=PREVIOUS_REVISION=100
```

This shifts traffic to an image that already exists; it does not rebuild, so it
takes seconds and cannot fail the way a re-deploy of an older commit can.

### What this does not cover

Only the API. The pipeline still runs on demand — see *Scheduling* below. A CD
pipeline that ships the serving layer while the data behind it is refreshed by
hand is a real gap, not an oversight to gloss over.

---

## Cost

| Service | Monthly |
| --- | --- |
| Artifact Registry | ~$0.10 |
| Cloud Storage (~16 MB) | <$0.01 |
| BigQuery | $0 (10 GiB storage, 1 TiB queries free) |
| Cloud Run | $0 (scales to zero) |
| Cloud Build | $0 (2,500 free minutes) |

The Cloud Storage and Artifact Registry free tiers are **US-region only**, so
both bill from the first byte here. At these volumes that's fractions of a
cent.

---

## Scheduling — DEPLOYED

**The pipeline has been running nightly since August.** This section said
otherwise until 2026-09-14, and so did every other document in this repo. It was
wrong. What actually exists:

| Resource | Value |
| --- | --- |
| Cloud Scheduler job | `world-genre-weekly`, **location `asia-southeast1`** |
| Schedule | `0 0 * * *`, time zone `Asia/Bangkok` → 17:00 UTC daily |
| Target | POST to `…/jobs/world-genre-pipeline:run` in `asia-southeast3` |
| Cloud Run Job | `world-genre-pipeline`, `asia-southeast3`, 1 CPU / 1 GiB |
| Timeout / retries | 7200s / `maxRetries: 1` |
| Service account | `411464225527-compute@developer.gserviceaccount.com` |
| `BQ_DATASET` | `world-genre-natt.world_genre` |
| `DATA_DIR` | `gs://world_genre_bucket/data` |
| `PUBLISH_DIR` | `gs://world-genre-serving/published` |
| `LASTFM_API_KEY` | Secret Manager, `lastfm-api-key:latest` |

The Scheduler job is **named** `world-genre-weekly` and runs **daily** — a
leftover from when it really was weekly, the same vintage as the `@weekly` that
was in the DAG until today. The cron is the truth; the name is not.

Note the two regions. Cloud Scheduler has no `asia-southeast3` location (run
`gcloud scheduler locations list`), so the trigger lives in `asia-southeast1`
and calls across. Looking for it in the job's own region finds nothing and
invites the conclusion that nothing is scheduled.

The job carries **no `command` and no `args`** — it runs the image's default
entrypoint, which is `scripts/run_pipeline.py`. One execution, all twelve
stages, about 36 minutes.

### The recovered sequence

Until 2026-09-14 the driver that ran these stages existed only inside the
container image; no Dockerfile in this repo built it, and it was not in git.
`backend/scripts/run_pipeline.py` is that sequence reimplemented from the job's
own Cloud Logging output, with the data-quality gate added before publish:

| Stage | Typical | Notes |
| --- | --- | --- |
| init_bq | 6s | idempotent, runs nightly |
| extract_kworb | 301s | |
| extract_lastfm | 131s | |
| extract_musicbrainz | 215s | |
| extract_deezer | 29s | |
| extract_wikidata | 8s | only deezer's misses |
| cleanse | 44s | |
| load | 44s | |
| **enrich_artists** | **1344s** | 62% of the run; see the rate-limit note below |
| enrich_genres | 4s | 147 of 149 genres have text |
| **validate** | — | new; blocks publish only |
| publish | 43s | |

### The MusicBrainz rate limit is an IP problem

`enrich_artists` spends 22 minutes to resolve ~53 artists against ~1,591
outstanding, almost all of it absorbing `503 rate-limited` responses. The
extractor is not at fault: it sends a descriptive User-Agent with a contact
address, honours `Retry-After`, and paces at ~1 req/sec, which is what
MusicBrainz asks for.

**MusicBrainz rate-limits per IP, and Cloud Run egress uses a shared address
pool.** The budget is not yours; you share it with whoever else is behind the
same address. The same code runs fine from a laptop. Options, none free: a
static egress IP via Cloud NAT (a fixed monthly charge that is material against
a project otherwise costing pennies — price it before committing), running that
one stage from somewhere with a stable address, or accepting that the artists
dimension may never fully converge and saying so rather than implying it will.

### Where Cloud Workflows fits now

`pipeline-workflow.yaml` describes a per-stage model: one Cloud Run Job
execution per stage, ordered and branched by Workflows. That is an *upgrade*
path, not the current design, and it is not deployed. Its advantage is per-stage
visibility and retries — with today's single execution, a failure in
`enrich_artists` and a failure in `publish` look identical from outside. Its
cost is that the gate's exit code becomes invisible (Workflows sees only "the
job failed"), so the 1-vs-2 distinction would need a BigQuery step querying
`dq_runs`.

**Cloud Composer is deliberately not used.** Airflow is free; Composer is not —
it bills for an always-on scheduler, web server and GKE cluster, roughly
$400/month to run a pipeline that executes for minutes a day. That is 40× the
rest of this project combined. `backend/dags/genre_pipeline_dag.py` stays in the
repo as the documented orchestration and for local `docker-compose` runs.

**Cloud Composer is deliberately not used.** Airflow is free; Composer is not —
it bills for an always-on scheduler, web server and GKE cluster, roughly
$400/month to run a pipeline that executes for minutes a day. That is 40× the
rest of this project combined. `backend/dags/genre_pipeline_dag.py` stays in the
repo as the documented orchestration and for local `docker-compose` runs.

Two honest limitations of the Workflows version, both in the file's header
comment: it sees only "the job failed", not the exit code, so `run_validate`'s
1-vs-2 distinction is invisible there (the fix, if it mattered, is a BigQuery
connector step querying `dq_runs`); and there is no backfill or task-level UI.

The DAG's `schedule` must match the Scheduler cron. They disagreed until
2026-09-14 (`@weekly` in the DAG, daily in production), which matters because
every threshold in `app/core/dq.py` is calibrated against daily partitions —
`chart_volume`'s trailing-7-day median would collapse to a single observation at
weekly cadence. Both now say daily. The DAG's `0 3 * * *` is UTC and does not
match production's midnight Bangkok (17:00 UTC); since the DAG is not deployed
this changes nothing operationally, but it is worth aligning next time the file
is touched.

### The pipeline image

`backend/Dockerfile.pipeline`, built by `cloudbuild.pipeline.yaml`. A third
image, deliberately: the Airflow image's entrypoint is Airflow's, and the API
image cannot be built without `frontend/dist` — a broken `npm run build` must
not be able to block a data pipeline deploy.

```powershell
gcloud builds submit --config cloudbuild.pipeline.yaml `
  --substitutions=_IMAGE=asia-southeast3-docker.pkg.dev/world-genre-natt/world-genre/pipeline
```

It installs `requirements-pipeline.txt` and `requirements-cloud.txt` only — no
fastapi, no uvicorn. `ENTRYPOINT ["python"]` is the contract with Workflows:
each step overrides the container args (`-m scripts.run_load`), so one job
definition covers all eleven stages. With no args it exits non-zero with a
message rather than opening a REPL and succeeding silently.

The same repo-root `.gcloudignore` governs this build; it excludes
`backend/data/` and `backend/tests/` and nothing the image COPYs, so no second
ignore file is needed.

Then the job:

```powershell
gcloud run jobs create world-genre-pipeline `
  --image=asia-southeast3-docker.pkg.dev/world-genre-natt/world-genre/pipeline `
  --region=asia-southeast3 `
  --task-timeout=3600 --max-retries=1 `
  --set-env-vars=BQ_DATASET=world-genre-natt.world_genre,DATA_DIR=gs://world_genre_bucket,PUBLISH_DIR=gs://world_genre_bucket/published

# Smoke test: this stage is idempotent and touches nothing else.
gcloud run jobs execute world-genre-pipeline --region=asia-southeast3 `
  --args=-m,scripts.run_init_bq --wait
```

**Then test one extractor from the job before building anything on top of it.**
It runs from a datacentre IP rather than a home connection; MusicBrainz
throttles those harder and scrapers sometimes block cloud ranges outright. If
`run_extract_kworb` fails there, the whole scheduling plan needs a different
shape, and that is much cheaper to learn now than after the workflow and
scheduler exist.

---

## Tearing down

```powershell
gcloud projects delete world-genre-natt
```

Deleting the project stops everything billable at once, including resources
you'd forgotten about. Individual deletes leave orphans.
