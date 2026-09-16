"""Run every pipeline stage in order - the entrypoint of the Cloud Run Job.

    python -m scripts.run_pipeline
    python -m scripts.run_pipeline --from load      # resume after a failure
    python -m scripts.run_pipeline --only publish   # one stage, by name
    python -m scripts.run_pipeline --list

This exists because the job that has been running nightly since August had no
equivalent in this repo. Its container ran a driver that printed stage banners
and timings, and that driver lived only inside the image - not in git, not in
any Dockerfile here. The sequence below was recovered from the job's own Cloud
Logging output and reimplemented, so that the image running production can
finally be rebuilt from source.

The stage order is not arbitrary and is worth knowing before editing it:

  kworb and lastfm are the only independent extracts. musicbrainz and deezer
  both prefer the mbids lastfm returns and fall back to kworb's chart rows, so
  they follow both. wikidata runs strictly after deezer because it only asks
  about the artists deezer failed to find a photo for. cleanse needs the three
  genre sources. load needs cleanse, and both enrichment stages use the rows
  load just wrote as their worklist. publish reads everything.

WHERE THE GATE SITS. run_validate goes between enrich_genres and publish. It
could run immediately after load - no check reads enrichment output - and that
would fail roughly twenty minutes earlier on a bad day. It runs here anyway,
because the enrichment stages are worth completing even when the day's chart
data is bad: merge_dimension preserves what they write, MusicBrainz resolutions
cost ~25 seconds each in this environment, and throwing an hour of them away to
punish a short scrape helps nobody. A failed gate skips publish and nothing
else. The gate blocks publishing, not the pipeline.

EXIT CODES, which the job's retry policy acts on:

    0  every stage succeeded and the gate passed
    1  a stage raised, or the gate failed on the data (deterministic)
    2  argparse usage error - NOT ours, and never worth retrying
    3  the gate could not run - transient, and the one case worth retrying

Note the job is currently configured with maxRetries=1, which means a
deterministic gate failure costs a second full 36-minute run to learn the same
thing. Worth revisiting once this is the deployed entrypoint.
"""
import argparse
import importlib
import logging
import time
from dataclasses import dataclass

# Configured HERE, before any stage module is imported, and that ordering is
# load-bearing. Each scripts/run_*.py calls logging.basicConfig at import time
# with a bare "%(levelname)s %(message)s"; basicConfig is a no-op once the root
# logger already has a handler, so configuring first is what gives every stage
# the timestamped format the nightly logs have always had. Import them first
# and the timestamps disappear from half the run.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("run_pipeline")

EXIT_OK = 0
EXIT_FAILED = 1
# 3, NOT 2, and the gap is deliberate. argparse exits 2 on a usage error, so
# a driver that also used 2 for "the gate could not run" would give an
# operator - and the job's retry policy - no way to tell a transient check
# failure (worth retrying) from a malformed command line (never worth
# retrying). That ambiguity cost a debugging session on 2026-09-16: a stage
# parsing the driver's own argv exited 2 and read as a gate error.
#
# run_validate keeps its own 0/1/2 contract; this driver translates.
EXIT_CHECK_ERROR = 3
VALIDATE_CHECK_ERROR = 2


@dataclass(frozen=True)
class Stage:
    """One pipeline step. `name` is what appears in the logs and in --from."""

    name: str
    module: str
    # The gate returns an exit code instead of raising, because "the data is
    # wrong" is not an exception - it is a verdict. Handled separately below.
    gate: bool = False
    # Its main() is main(argv) and reads sys.argv when argv is None, so it
    # MUST be called with [] explicitly.
    #
    # This used to be implied by `gate`, and the two are not the same thing.
    # run_resolve_artists has an argv signature (for --dry-run) and is not a
    # gate, so it was called as main() and argparse inside it parsed THIS
    # script's arguments - rejecting `--from load` with argparse's own exit
    # code 2. One flag standing for two unrelated properties is how a new
    # stage inherits the wrong half of it. test_run_pipeline.py now checks
    # every stage's real signature against this field, so a mismatch fails in
    # CI rather than in a Cloud Run job.
    takes_argv: bool = False


STAGES: list[Stage] = [
    # Idempotent, and cheap enough (~6s) to run every night rather than being
    # a setup step someone has to remember after adding a table.
    Stage("init_bq", "scripts.run_init_bq"),
    Stage("extract_kworb", "scripts.run_extract_kworb"),
    Stage("extract_lastfm", "scripts.run_extract_lastfm"),
    Stage("extract_musicbrainz", "scripts.run_extract_musicbrainz"),
    Stage("extract_deezer", "scripts.run_extract_deezer"),
    Stage("extract_wikidata", "scripts.run_extract_wikidata"),
    Stage("cleanse", "scripts.run_cleanse"),
    Stage("load", "scripts.run_load"),
    # Warehouse-side resolution against the MusicBrainz mirror, before the API
    # stage rather than instead of it. Everything this resolves is an artist
    # enrich_artists never has to spend a rate-limited request on, so its
    # position here - after load, before enrichment - is the whole saving.
    Stage("resolve_artists", "scripts.run_resolve_artists", takes_argv=True),
    # The long pole: ~22 minutes of the ~36 minute run, and it resolves only
    # tens of artists per night because MusicBrainz rate-limits per IP and
    # serverless egress shares its addresses. See DEPLOYMENT.md.
    Stage("enrich_artists", "scripts.run_extract_artist_meta"),
    Stage("enrich_genres", "scripts.run_extract_genre_info"),
    Stage("validate", "scripts.run_validate", gate=True, takes_argv=True),
    Stage("publish", "scripts.run_publish"),
]

STAGE_NAMES = [s.name for s in STAGES]


def select_stages(
    stages: list[Stage], start: str | None = None, only: str | None = None
) -> list[Stage]:
    """Which stages to run. Pure, so the selection logic is testable.

    `only` wins over `start`; both are validated by name so a typo fails
    immediately rather than silently running the whole pipeline.
    """
    if only is not None:
        matched = [s for s in stages if s.name == only]
        if not matched:
            raise ValueError(f"unknown stage {only!r}; expected one of {STAGE_NAMES}")
        return matched
    if start is not None:
        names = [s.name for s in stages]
        if start not in names:
            raise ValueError(f"unknown stage {start!r}; expected one of {STAGE_NAMES}")
        return stages[names.index(start):]
    return list(stages)


def run_stage(stage: Stage) -> int | None:
    """Execute one stage, logging the banner and timing the nightly logs carry.

    Imported here rather than at module scope so that a broken import in one
    stage fails that stage rather than preventing the driver from starting at
    all - the same reasoning as the imports inside the Airflow DAG's tasks.
    """
    logger.info("--- %s ---", stage.name)
    started = time.monotonic()
    main = importlib.import_module(stage.module).main

    # [] rather than nothing for any stage whose main() takes argv: given
    # None, argparse falls back to sys.argv and parses THIS script's
    # arguments, rejecting `--from load` as unrecognised.
    result = main([]) if stage.takes_argv else main()

    logger.info("%s done in %ds", stage.name, round(time.monotonic() - started))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="start", help="resume from this stage onward")
    parser.add_argument("--only", help="run exactly one stage")
    parser.add_argument(
        "--list", action="store_true", help="print the stage order and exit"
    )
    args = parser.parse_args(argv)

    if args.list:
        for stage in STAGES:
            print(f"{stage.name:22} {stage.module}")
        return EXIT_OK

    try:
        selected = select_stages(STAGES, start=args.start, only=args.only)
    except ValueError as exc:
        parser.error(str(exc))

    started = time.monotonic()

    for stage in selected:
        try:
            result = run_stage(stage)
        except Exception:
            # Logged with a traceback and then fatal: every later stage reads
            # what an earlier one wrote, so continuing past a failure would
            # publish a half-built day rather than yesterday's complete one.
            logger.exception("%s failed - stopping before the remaining stages", stage.name)
            return EXIT_FAILED

        if stage.gate and result:
            # The only non-fatal-looking failure that still stops the run.
            # Everything up to here kept its work; publish is what gets
            # skipped, which leaves the previous run's JSON serving.
            logger.error(
                "gate blocked the run (exit %d) - skipping publish; the API keeps "
                "serving the previous run's payloads",
                result,
            )
            return (
                EXIT_CHECK_ERROR if result == VALIDATE_CHECK_ERROR else EXIT_FAILED
            )

    logger.info(
        "pipeline done in %ds (%d stages)",
        round(time.monotonic() - started),
        len(selected),
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
