"""Orchestrates the pipeline scripts in backend/scripts/. Each task just calls
that script's existing main() - no logic is duplicated here.

NOT DEPLOYED. Cloud Composer bills for an always-on environment (~$400/mo for
a pipeline that runs for minutes a day), so production scheduling is planned as
Cloud Run Jobs driven by Cloud Workflows - see pipeline-workflow.yaml at the
repo root and DEPLOYMENT.md section "Scheduling". This DAG stays in the repo as
the documented orchestration, and runs locally via docker-compose.

Dependency order:
  kworb        -> no deps, writes data/raw/kworb/*.json
  lastfm       -> no deps, writes data/raw/lastfm/*.json
  musicbrainz  -> prefers data/raw/lastfm/*.json (mbids), falls back to
                  data/raw/kworb/*.json            (depends on both)
  deezer       -> prefers data/raw/lastfm/*.json,
                  falls back to data/raw/kworb/*.json  (depends on both)
  cleanse      -> normalizes genres/artist names, merges the Last.fm +
                  MusicBrainz genre signals into data/processed/*.json, and
                  writes data/processed/_quality_report.json - see
                  app/services/cleansing.py (depends on kworb, lastfm,
                  musicbrainz; independent of deezer, which only feeds images)
  wikidata     -> fills in artist photos deezer had none for, looked up by
                  MusicBrainz id on Wikidata/Commons (depends on deezer,
                  since it only asks about deezer's misses)
  ensure_schema-> applies sql/bigquery/schema.sql (idempotent) so a fresh clone
                  or a new dataset doesn't fail the load with "Not found:
                  Table"
  load         -> writes data/processed/*.json into the BigQuery warehouse -
                  facts by delete-partition-and-append, dimensions by MERGE.
                  Also loads the cleanse quality report into cleanse_quality.
                  Depends on cleanse + ensure_schema.
  enrich_*     -> fill the artists and genres dimensions, using what load just
                  wrote as their worklist
  validate     -> the data-quality gate. Blocks publish, not the pipeline.
  publish      -> runs every read query once and writes the API payloads as
                  static JSON to the serving bucket

Requires docker-compose.yaml to mount ./app, ./scripts, ./seeds, ./data into
the containers, PYTHONPATH=/opt/airflow set, and the BigQuery environment in
place: BQ_DATASET (or BQ_PROJECT), DATA_DIR/PUBLISH_DIR if they should point at
GCS rather than the local volume, and Application Default Credentials mounted
so google-cloud-bigquery can authenticate. Note ADC is a separate credential
store from the gcloud CLI's - a working `bq` on the host proves nothing about
what the container can do.
"""
from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.decorators import dag, task
from airflow.exceptions import AirflowException, AirflowFailException


@dag(
    dag_id="genre_pipeline",
    schedule="@weekly",              # or a cron string, e.g. "0 3 * * 1"
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    tags=["genre-pipeline"],
    default_args={
        # Safety net on top of musicbrainz.py's own in-request retries -
        # covers a whole task run dying (e.g. many consecutive timeouts).
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    },
)
def genre_pipeline():
    # Imports happen inside each task (not at module top) so the DAG parses
    # fast and a broken import only fails that one task run, not the whole
    # DAG's presence in the UI.

    @task
    def extract_kworb():
        from scripts.run_extract_kworb import main
        main()

    @task
    def extract_lastfm():
        from scripts.run_extract_lastfm import main
        main()

    @task
    def extract_musicbrainz():
        from scripts.run_extract_musicbrainz import main
        main()

    @task
    def extract_deezer():
        from scripts.run_extract_deezer import main
        main()

    @task
    def cleanse():
        from scripts.run_cleanse import main
        main()

    @task
    def extract_wikidata():
        from scripts.run_extract_wikidata import main
        main()

    @task
    def ensure_schema():
        """Applies sql/bigquery/schema.sql before loading.

        Every statement in it is CREATE TABLE IF NOT EXISTS, so this is a
        cheap no-op on all runs after the first. It exists so a fresh clone,
        or a dataset that has gained a table since (dq_runs and
        cleanse_quality were both added after the original nine), doesn't
        fail the load with "Not found: Table" just because nobody remembered
        to run scripts/run_init_bq.py by hand.

        BigQuery has no indexes to create - partitioning and clustering
        replace them - so unlike the Postgres original this only ever creates
        tables.
        """
        from scripts.run_init_bq import main
        main()

    @task
    def load():
        from scripts.run_load import main
        main()

    @task(retries=0)
    def validate():
        """The data-quality gate: assert on the warehouse before publishing.

        `retries=0` overrides the DAG default deliberately. A failed
        assertion is DETERMINISTIC - the same query against the same
        partition returns the same number - so retrying twice at five-minute
        intervals only delays the bad news by ten minutes. Retries are for
        transient failures, and a data-quality failure is the opposite of
        transient.

        But a check that could not RUN is transient, which is why
        run_validate distinguishes the two in its exit code. Exit 2 raises a
        normal AirflowException and, with retries restored for that case by
        the raise below, would be worth retrying; exit 1 raises
        AirflowFailException, which Airflow treats as terminal and does not
        retry regardless of the retry policy. That distinction is the whole
        reason the script returns 1 and 2 rather than just non-zero.
        """
        from scripts.run_validate import EXIT_CHECK_ERROR, main

        code = main()
        if code == EXIT_CHECK_ERROR:
            # Transient: credentials, network, a BigQuery hiccup. Worth
            # another attempt, so a retryable exception.
            raise AirflowException(
                "Data-quality checks could not run (exit 2). See dq_runs for "
                "the recorded errors."
            )
        if code:
            # Terminal: the data is wrong. AirflowFailException skips the
            # retry policy entirely rather than relying on retries=0, so this
            # stays correct if someone raises the DAG default later.
            raise AirflowFailException(
                f"Data-quality gate blocked the run (exit {code}). Publish is "
                "skipped; the API keeps serving the previous run's JSON."
            )

    @task
    def publish():
        """Runs every read query once and writes the API payloads as JSON.

        The last stage, and the one that decouples serving from the
        warehouse: BigQuery answers in 0.5-2s regardless of table size, so
        the API reads published files instead of querying. Depends on the
        enrichment tasks as well as load, because origin_country and genre
        descriptions have to be in place before the payloads are built - a
        publish that ran first would bake in nulls until tomorrow.
        """
        from scripts.run_publish import main
        main()

    @task
    def enrich_genres():
        """Genre descriptions from Last.fm's tag.getInfo.

        Bounded by the taxonomy (~150 buckets) rather than by chart size, and
        cached, so this is a one-time cost that later runs skip entirely.
        """
        from scripts.run_extract_genre_info import main
        main()

    @task
    def enrich_artists():
        """Fills the artists dimension (origin country, formation year).

        Runs AFTER load because its worklist is the artists load just wrote,
        and it's bounded to a fixed number of lookups per run - MusicBrainz's
        ~1 req/sec limit makes resolving every charting artist a multi-hour
        job, so the dimension fills in across successive weekly runs instead.
        """
        from scripts.run_extract_artist_meta import main
        main()

    kworb = extract_kworb()
    lastfm = extract_lastfm()
    musicbrainz = extract_musicbrainz()
    deezer = extract_deezer()
    wikidata = extract_wikidata()
    cleansed = cleanse()
    schema = ensure_schema()
    loaded = load()
    enriched = enrich_artists()
    genre_info = enrich_genres()
    validated = validate()
    published = publish()

    [kworb, lastfm] >> musicbrainz
    [kworb, lastfm] >> deezer
    # Strictly after deezer, not in parallel: it only queries Wikidata about
    # the artists deezer failed to find a photo for, so it needs deezer's
    # output to know what's missing.
    deezer >> wikidata
    [kworb, lastfm, musicbrainz] >> cleansed
    [cleansed, schema] >> loaded

    # Validate runs BESIDE the enrichment tasks, not before them, and this is
    # a deliberate choice rather than an accident of layout.
    #
    # None of the checks read enrichment output - they assert on what load
    # wrote - so the gate has everything it needs the moment load finishes,
    # and running it in parallel means a bad partition is known in minutes
    # instead of after the rate-limited crawls.
    #
    # The enrichment tasks are NOT downstream of it on purpose. Their work is
    # valuable even on a day whose chart data is bad: merge_dimension keeps
    # what they write, MusicBrainz is ~1 req/sec, and discarding an hour of
    # lookups because today's scrape came back short would be throwing away
    # good data to punish bad data. The gate blocks PUBLISH, not the pipeline.
    loaded >> [enriched, genre_info, validated]

    # Publish needs all three: the two enrichments for content, and the gate
    # for permission. Airflow's default all_success trigger rule is what makes
    # the gate binding - a failed validate leaves publish upstream_failed, and
    # the serving bucket keeps yesterday's payloads untouched.
    [enriched, genre_info, validated] >> published


genre_pipeline()
