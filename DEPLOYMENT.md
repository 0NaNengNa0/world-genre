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

Nine tables, all `CREATE TABLE IF NOT EXISTS`, so this is safe on every run.
Fact tables partition on `snapshot_date` and cluster on `country_code` —
partition pruning is what keeps reads cheap, since BigQuery bills on bytes
scanned.

Dataset names allow only letters, numbers and underscores. The format is
`project:dataset` — project on the left.

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

`cleanup.json` keeps the three most recent versions and deletes untagged ones
after 7 days. `Keep` is evaluated before `Delete`, so recent versions survive
regardless. Add `--dry-run` to see what would be removed before committing.

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

## Scheduling (not deployed)

The pipeline currently runs on demand. To automate it, each stage becomes a
Cloud Run **Job** — not a Service, because jobs run to completion and allow long
timeouts, and the MusicBrainz stage alone runs 33 minutes cold. Cloud Scheduler
triggers the chain daily. Cost is effectively zero.

This needs a pipeline image, which doesn't exist yet — `Dockerfile.api` builds
the API, and the Airflow image isn't the right shape for a job.

**Test one extractor from a Cloud Run Job before relying on it.** They'd be
running from a datacentre IP rather than a home connection; MusicBrainz
throttles those harder and scrapers sometimes block cloud ranges outright.

Cloud Composer is deliberately not used: it bills ~$400/month for an always-on
cluster, 40× everything else here combined. The DAG stays in the repo as the
documented orchestration.

---

## Tearing down

```powershell
gcloud projects delete world-genre-natt
```

Deleting the project stops everything billable at once, including resources
you'd forgotten about. Individual deletes leave orphans.
