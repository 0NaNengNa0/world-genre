"""
Orchestrates the extract scripts that already live in backend/scripts/.
No logic is duplicated here - each task just calls that script's existing
main(). This replaces run_extract_kworb.py / run_extract_lastfm.py /
run_extract_deezer.py that were sitting in this folder as exact copies of
the scripts/ versions - those aren't real DAGs (no DAG object in them), so
Airflow was never picking them up anyway. Harmless to leave in place, or
delete manually whenever you like.

Spotify has been dropped as a source (it stopped exposing chart/genre data
to third-party dev-mode apps in Feb 2026) and replaced with MusicBrainz -
see scripts/run_extract_musicbrainz.py and app/services/extractors/spotify.py
(now unused, safe to delete manually).

Dependency order (matches what each script's own docstring already assumes):
  kworb        -> no deps, writes data/raw/kworb/*.json
  lastfm       -> no deps, writes data/raw/lastfm/*.json
  musicbrainz  -> prefers data/raw/lastfm/*.json (mbids), falls back to
                  data/raw/kworb/*.json            (depends on both)
  deezer       -> prefers data/raw/lastfm/*.json,
                  falls back to data/raw/kworb/*.json  (depends on both)

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

    kworb = extract_kworb()
    lastfm = extract_lastfm()
    musicbrainz = extract_musicbrainz()
    deezer = extract_deezer()

    [kworb, lastfm] >> musicbrainz
    [kworb, lastfm] >> deezer


genre_pipeline()
