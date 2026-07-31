"""
Orchestrates the extract scripts in backend/scripts/. Each task just calls
that script's existing main() - no logic is duplicated here.

Dependency order:
  kworb        -> no deps, writes data/raw/kworb/*.json
  lastfm       -> no deps, writes data/raw/lastfm/*.json
  musicbrainz  -> prefers data/raw/lastfm/*.json (mbids), falls back to
                  data/raw/kworb/*.json            (depends on both)
  deezer       -> prefers data/raw/lastfm/*.json,
                  falls back to data/raw/kworb/*.json  (depends on both)
  cleanse      -> normalizes genres/artist names and merges the Last.fm +
                  MusicBrainz genre signals into data/processed/*.json -
                  see app/services/cleansing.py (depends on kworb, lastfm,
                  musicbrainz; independent of deezer, which only feeds
                  images, read directly by app/services/countries.py)

Requires docker-compose.yaml to mount ./app, ./scripts, ./seeds, ./data
into the containers and PYTHONPATH=/opt/airflow set - both already added.
"""
from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.decorators import dag, task


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

    kworb = extract_kworb()
    lastfm = extract_lastfm()
    musicbrainz = extract_musicbrainz()
    deezer = extract_deezer()
    cleansed = cleanse()

    [kworb, lastfm] >> musicbrainz
    [kworb, lastfm] >> deezer
    [kworb, lastfm, musicbrainz] >> cleansed


genre_pipeline()
