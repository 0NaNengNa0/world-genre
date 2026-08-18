# Architecture

How the system is put together, and which decisions were non-obvious enough to
be worth recording.

For setup see [README.md](README.md); for infrastructure see
[DEPLOYMENT.md](DEPLOYMENT.md).

---

## Shape

```
extract ──→ data lake ──→ cleanse ──→ warehouse ──→ publish ──→ serve
 (5 APIs)     GCS          local       BigQuery       GCS      Cloud Run
```

Six stages, each with a single job. Extractors only fetch and cache — nothing
in them knows what a genre score is. Cleansing is pure computation over local
files. The warehouse holds facts. Publishing turns facts into API payloads.
Serving is a file read.

That separation is what makes the pipeline restartable at any stage, which
matters when one stage takes 33 minutes.

---

## The data lake

Extractors write JSON under `DATA_DIR`, a GCS bucket. Every per-artist lookup
is cached as its own object — 1,059 files each for MusicBrainz and Deezer.

**Caching is correctness here, not optimisation.** MusicBrainz allows ~1
request per second. A cold crawl of every charting artist is 33 minutes; a
warm one is seconds. Cloud Run's filesystem is ephemeral, so if that cache
lived in the container it would vanish between runs and every single run would
pay the full 33 minutes. Putting `DATA_DIR` in a bucket is what makes the
pipeline affordable to run daily.

The whole codebase reaches storage through `pathlib`'s interface —
`/`, `read_text`, `write_text`, `exists`, `mkdir`, `glob`, `open`, `stat` — and
nothing else. No `os.path`, no `shutil`. `cloudpathlib` implements exactly that
surface, so all 78 call sites are unaware of whether they're on disk or in a
bucket; only the root object differs. A test pins that operation set so adding
an incompatible call fails in CI rather than inside a Cloud Run job.

**Cache entries record how they were resolved.** The Deezer cache carries a
`MATCH_VERSION`. When the matching rule changed, entries chosen under the old
rule were refetched rather than trusted — they contain no evidence of their own
reliability, so there is no way to tell a good one from a bad one after the
fact.

---

## Scoring

### Genre buckets

MusicBrainz has ~2,200 genres. Left alone the long tail fragments every
country's profile into noise, so a curated ~150-entry taxonomy
(`seeds/genre_buckets.txt`) collapses them, with fuzzy matching for near
misses. About 18% of tags stay unclassified, which the pipeline reports rather
than hides.

### Distinctiveness

TF-IDF, applied to countries instead of documents:

```
distinctiveness = score × ln(total_countries / countries_with_genre)
```

**Thresholds are share-based, not absolute — and that was a real bug.** When
the sample deepened from 20 to 100 artists per country, absolute floors stopped
meaning the same thing: Japan's `j-pop` collapsed to zero distinctiveness
despite being 11% of its plays, while `bossa nova` at 0.4% was promoted to its
most distinctive genre. Both the document-frequency count and the noise floor
now work in shares, so sample depth no longer changes the answer.

That is the shape of the worst bugs in this project: nothing errored, the
numbers were just wrong.

---

## Warehouse

Nine BigQuery tables. One fact table, the rest dimensional or derived.

`chart_entries` is the fact table: one row per track per country per day,
carrying **measured** quantities — streams — rather than scores this project
invented. Keeping it at the finest available grain is what makes "streams by
genre", "domestic share" and "chart churn" all answerable from one table
instead of needing a new pipeline each.

### What BigQuery changes about warehouse design

| | Consequence |
| --- | --- |
| `PRIMARY KEY` is `NOT ENFORCED` | uniqueness is the loader's job |
| No indexes | partition + cluster; pruning is a *cost* reduction |
| No upsert | facts rewritten a partition at a time |
| Billed on bytes scanned | every read wants a `snapshot_date` predicate |

**Uniqueness has to be constructed.** BigQuery accepts key declarations and
never checks them — they exist for the optimizer. A duplicate row isn't
rejected, it simply becomes a second row, and every downstream count doubles
silently. `run_load` deduplicates in Python before writing.

**Idempotency has two shapes, and confusing them is destructive.** Facts are
rewritten a whole day at a time: delete the partition, then append. Dimensions
can't be, because their columns are filled in by *different* scripts across
*different* runs — `run_extract_artist_meta` writes `origin_country`,
`run_extract_deezer` writes `deezer_fans`. A truncate-and-load on `artists`
would look like a clean run while discarding hours of rate-limited enrichment,
taking the domestic-share metric with it. Those get `MERGE` with an explicit
list of columns the caller owns.

---

## Why the API doesn't query the warehouse

BigQuery answers in **0.5–2 seconds regardless of table size**, because it is
an analytical engine with no point lookups. A country detail page needs about
six queries. Serving requests from it directly would mean multi-second clicks
and a scan charge on every one.

The data changes once a day. So the queries run once at pipeline time and the
API payloads are written as static JSON to a serving bucket. Response time
drops to ~20ms, cost to nothing, and the request path contains no database at
all — no pool, no cursor, no SQL.

**This required getting the fan-out right, and I got it wrong first.** Moving
queries out of the request path is only half the win; the publish step
initially ran them per country *and per genre* — about 1,748 serial jobs, 15 to
58 minutes. Batching the genre panels into one array-parameter query per
country and running countries concurrently brought that to 537 jobs in about a
minute. Fixing a per-request cost by creating a per-run cost of the same shape
is not a fix.

### Published layout

```
countries.json          every country summary
country/{code}.json     full detail, genre panels nested
artists-global.json     worldwide ranking
genres-trending.json    biggest movements
```

Few large files rather than many small ones. Genre panels live inside the
country payload because they're only ever opened from a country that has just
been fetched — a per-genre object would mean a publish step scaling with
genres × countries.

The reader caches with a TTL. Without one it would serve whatever existed at
container start, indefinitely, since nothing restarts the API when the pipeline
runs.

---

## Serving

One Cloud Run service runs FastAPI and the built SPA together.

**Same-origin, which removes CORS entirely.** No allow-list to keep in sync
with a deploy — the failure mode where the API is fine and the site looks
broken. The frontend's relative `/api` paths work in production exactly as they
do behind the Vite dev proxy.

Two guards in the catch-all route, both for failures that mislead rather than
fail cleanly. An unknown `/api/*` path returns 404 instead of falling through
to `index.html` with status 200 — a client would otherwise see "unexpected
token <" and go looking in the wrong place. Same for a missing asset.

---

## SQL conventions

SQL lives in `.sql` files rather than Python strings, so each query is
independently runnable and reviewable.

**Substitution is `str.replace`, never `str.format`.** A format field collides
with any brace elsewhere in the file, and these files are full of prose that
mentions things like `{code}`.

**No literal `%` anywhere, including comments.** A driver that scans query
strings for placeholders reads a percent sign in prose as a malformed
parameter.

The same class of bug recurred during development with a *semicolon*: a comment
containing `schema.sql; this is a port` was split as a statement boundary,
handing a fragment of English to BigQuery. Comments are now stripped before
splitting. **Characters that are inert in prose but load-bearing to a parser
are this codebase's most repeated mistake** — which is why the tests check for
them by name.

---

## Testing

Unit tests for pure logic, contract tests over published payloads, and offline
dialect validation for SQL.

BigQuery has no local emulator, so SQL correctness is checked by parsing every
query in the BigQuery dialect and rejecting nine constructs BigQuery does not accept, by
name. Parsing alone is insufficient — `FILTER (WHERE ...)` is valid standard
SQL that parses cleanly and is rejected by BigQuery at runtime. It survived
into three files and failed the first real publish.

Where a rewrite had to preserve semantics, equivalence was **verified rather
than assumed**: the `FILTER` → `COUNTIF` / `CASE WHEN` conversions were run
side by side in DuckDB over data with the awkward cases — NULL measures, a
group where nothing matches, repeated values under `COUNT(DISTINCT)` — and
checked to agree.

---

## Known limitations

**Deezer's name-only matching is wrong about 29% of the time.** Documented in
the README with the evidence. The code is fixed; the data still needs a re-run.

**Last.fm's 26% overlap with chart artists is not a defect** — it measures
scrobbles, a different population from Spotify streams. That disagreement is
why the UI shows three sources side by side and never adds them together.

**Artist origins are incomplete by design**, filling in across runs against
MusicBrainz's rate limit. Coverage is reported alongside the figure it
qualifies.
