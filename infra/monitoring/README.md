# Monitoring: who watches a pipeline that never ran

Everything else watching this pipeline runs **inside** it. The data-quality
gate is stage twelve of thirteen — it measures today's data, records a verdict
in `dq_runs`, and blocks the publish when the numbers are wrong. It is good at
that, and it is structurally incapable of the one question this directory
exists to answer:

> Did the pipeline run at all?

A check inside the pipeline cannot fire when the pipeline doesn't run. Silence
from the gate is indistinguishable from "not yet". That is not a gap in the
gate's coverage; it is a property of where it lives. **An observer inside the
system cannot report on the system's absence.**

This project has already paid for that lesson twice. The pipeline ran nightly
on Cloud Run since August with no document in the repo saying so. The
enrichment backlog drifted from 1,591 to 1,602 over weeks with nothing
watching the trend. Both were invisible for the same reason: nothing outside
the thing was looking at it.

## Two alarms, because there are two failures

They are not redundant, and neither one can see what the other sees.

| | fires when | latency | blind to |
| --- | --- | --- | --- |
| `alert_pipeline_failed.json` | an execution finishes with `result=failed` | ~10 min | a run that never started |
| `alert_pipeline_stale.json` | no successful completion logged for 30h | up to 30h | which stage broke, or why |

The fast one watches Cloud Run's own `completed_execution_count`, so it needs
a run to have happened. **Every interesting way this pipeline dies is invisible
to it**: the scheduler paused and never resumed, the job deleted, the image tag
pointing at nothing, a run hanging until its 7200s timeout. In all of those the
failure count stays at zero, forever, looking exactly like a healthy week.

The slow one is the answer to those, and it works by **absence** — a condition
that fires when a time series stops producing data rather than when a value
crosses a line:

```
metric:    logging.googleapis.com/user/world_genre_pipeline_completed
align:     ALIGN_SUM over 86400s   -> "completions in the trailing 24h"
condition: < 1, sustained for 21600s (6h)
missing:   EVALUATION_MISSING_DATA_ACTIVE
```

### Reading that condition

The aligned value is *how many successful runs finished in the last 24 hours* —
normally 1. When a night is missed it falls to 0 and stays there, and after a
further 6 hours the policy fires. **Alignment window plus duration is the real
staleness budget: 24h + 6h = 30h since the last completion.** That composition
is the whole trick, and it is worth knowing generally: a single condition's
`duration` is capped at 24 hours, so any staleness window longer than a day has
to be built from the alignment period and the duration together.

`evaluationMissingData: EVALUATION_MISSING_DATA_ACTIVE` is what makes this an
absence alarm rather than a threshold one. Without it the condition silently
never evaluates when the metric stops reporting — no series, nothing to compare
— which is precisely the state we are trying to detect. Missing data IS the
signal here, so it has to be declared as a violation rather than as a gap.

### What we tried first, and why it was rejected

The obvious expression is PromQL:

```promql
absent_over_time(logging_googleapis_com:user_world_genre_pipeline_completed[30h])
```

Cloud Monitoring accepts the metric name and then rejects the policy:

    INVALID_ARGUMENT: Log-based metrics are not supported in PromQL alerts
    with lookback windows longer than 1d1h.

25 hours is the ceiling, and with completions ~24h apart that leaves one hour of
tolerance — a single slow run away from a false alarm. The threshold form above
has no such cap because the 24h lives in the alignment period, which is not a
lookback window.

### Why 30 hours and not 24

The job is scheduled daily at 17:00 UTC and takes roughly half an hour, so
consecutive *completions* sit about 24 hours apart. A 24-hour budget would
alarm on ordinary jitter — a slightly slow run, a cold start, a retry — and an
alert that cries wolf is worse than no alert, because it trains you to ignore
it. 30 hours absorbs one late run and still catches a missed day long before
the second one.

This is the same reasoning as the gate's thresholds: pick the bound from the
observed behaviour of the system, not from the round number nearest your
intuition. Which also means it is only as good as the observation — the ~30
minute figure predates the MusicBrainz join that cut the enrichment backlog
from 1,602 to 1,020, so re-measure the run duration and re-derive this if it
has moved much.

### The first alert will be a true statement about nothing

A log-based metric only counts lines written *after* the metric was created, so
until the first scheduled run completes there is no data — and with missing
data treated as a violation, the policy fires. That is not a false positive: no
successful run has been observed. It is just not interesting. It closes itself
once the first run lands.


## What the signal actually is

The last line `run_pipeline` prints on success:

```python
logger.info("pipeline done in %ds (%d stages)", ...)
```

It is emitted only after every stage returned **and** the gate allowed the
publish. A run that crashed in stage three, or that the gate blocked, never
logs it. So the metric means *"a complete, publishable run finished"* — not
*"the container exited"*.

That distinction matters more than it first looks. Cloud Run's own
`completed_execution_count{result=ok}` would tick for `--only publish`, for a
run that skipped twelve stages, for anything that exited zero. It cannot see
the gate's verdict at all. A pipeline that "succeeds" every night while
publishing nothing would keep that metric perfectly green.

**The cost of this choice**: matching on log text couples the alert to a
string. Rename the line and the metric silently stops collecting, which fails
in the direction that looks healthy — no data points, and an absence alert with
nothing to compare against. The compensating control is
`TestCompletionLineIsAMonitoringContract` in `backend/tests/test_run_pipeline.py`,
which pins the string and asserts it is *not* emitted on a blocked gate or a
raised stage. Rename it and CI fails in the same commit.

The better version of this is a structured log field, or writing a row to a
`pipeline_runs` table and alerting on its max timestamp. Both are real
improvements and neither is free; this is the version that shipped today.

## Applying it

```powershell
cd infra\monitoring
.\apply.ps1
```

Re-runnable: each step finds the existing resource and updates it. That matters
most for the policies — Cloud Monitoring identifies a policy by a generated
name, *not* by `displayName`, so a plain `policies create` run twice leaves you
with two identical policies that both page you.

Verify:

```powershell
gcloud alpha monitoring policies list --project=world-genre-natt --format="table(displayName,enabled)"
gcloud logging metrics describe world_genre_pipeline_completed --project=world-genre-natt
```

## Testing that an alert actually fires

An untested alert is a belief, not a control. The cheap version:

1. Confirm the metric has data — it should have one point per night:
   `gcloud logging read "resource.type=cloud_run_job AND textPayload:pipeline" --freshness=3d --limit=5`
2. Pause the scheduler for a day and watch the staleness alert arrive on the
   30-hour mark. Slow, but it is the only honest test of an absence condition.
3. For the fast alert, run a stage that will fail — e.g. execute the job with
   `--args="-m,scripts.run_pipeline,--only,nonexistent"` — and expect mail
   within ~10 minutes.

## Why this is a script and not Terraform

Terraform is the right long-term home (see the IaC gap in
`claude/backend-map.md`), and this script has the weakness you would expect: it
drifts the moment someone edits a policy in the console, and it has no state
file to notice. The argument for waiting is real.

It lost on the specific history of this project. Production ran undocumented
for weeks *because* infrastructure existed only in a console, and "we'll codify
it properly later" is the sentence that produced that situation. A committed
script is not IaC, but it is reproducible, reviewable and diffable, which is
most of what was actually missing. The JSON bodies map field-for-field onto
`google_monitoring_alert_policy`, so migrating means rewriting the wrapper, not
the thinking.
