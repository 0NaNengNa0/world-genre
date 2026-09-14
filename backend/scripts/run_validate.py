"""Validate the warehouse before publishing - the gate stage.

    python -m scripts.run_cleanse
    python -m scripts.run_load
    python -m scripts.run_validate     # <- here
    python -m scripts.run_publish

Exits non-zero when a blocking check fails, so an orchestrator that runs the
stages in sequence stops before run_publish. That is the entire point: the API
serves static JSON, so a publish that never happens leaves yesterday's good
data in place. Failing here costs a day of freshness; failing to fail here
costs correctness, and nobody finds out until someone notices Brazil has four
genres.

    python -m scripts.run_validate --date 2026-09-13   # re-check an older day
    python -m scripts.run_validate --no-record         # don't write dq_runs
    python -m scripts.run_validate --fail-on-warn      # treat warnings as fails

Results are written to dq_runs whether they pass or fail, and are written
before the exit code is decided - a failed run that records nothing about why
it failed is the version of this stage that helps no one.
"""
import argparse
import logging
from datetime import date, datetime, timezone

from app.core import dq

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("run_validate")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--date",
        dest="snapshot_date",
        type=date.fromisoformat,
        default=None,
        help="Partition to validate (default: today, UTC - what run_load just wrote)",
    )
    parser.add_argument(
        "--no-record",
        action="store_true",
        help="Skip writing results to dq_runs (for a local dry run)",
    )
    parser.add_argument(
        "--fail-on-warn",
        action="store_true",
        help="Exit non-zero on warnings too",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # UTC and date-only, matching run_load.main - the partition being checked
    # has to be the one that was just written, and a local-time "today" is a
    # different day for several hours in Bangkok.
    snapshot_date = args.snapshot_date or datetime.now(timezone.utc).date()

    logger.info("Validating %s (%d checks)", snapshot_date, len(dq.CHECKS))
    results = dq.run_checks(snapshot_date)

    for result in results:
        level = logging.ERROR if result.blocked else (
            logging.WARNING if result.status == dq.WARN else logging.INFO
        )
        logger.log(level, "%s %s", result.summary(), result.context or "")

    if not args.no_record:
        try:
            dq.record(results, snapshot_date)
        except Exception as exc:  # noqa: BLE001
            # Recording is observability, not the gate. Losing the audit row is
            # bad; turning a green run red because the audit table was missing
            # would be worse, and would teach everyone to pass --no-record.
            logger.warning("could not write dq_runs: %s", exc)

    counts = dq.tally(results)
    # Skips are reported separately and never folded into "passed". A check
    # that abstained verified nothing, and a summary line that counts it as a
    # pass is the exact false reassurance this stage exists to prevent.
    logger.info(
        "%d passed, %d skipped, %d warned, %d failed, %d errored.",
        counts[dq.PASS],
        counts[dq.SKIP],
        counts[dq.WARN],
        counts[dq.FAIL],
        counts[dq.ERROR],
    )

    failed = [r for r in results if r.status == dq.FAIL]
    errored = [r for r in results if r.status == dq.ERROR]

    if errored:
        # Separated from failures because they route differently: this is a
        # credentials, network or SQL problem, not a statement about the data.
        logger.error(
            "Could not run: %s. Treated as blocking - a check that did not "
            "execute has not passed.",
            ", ".join(r.check.name for r in errored),
        )
    if failed:
        logger.error(
            "Blocking: %s. Not safe to publish - the API keeps serving the "
            "previous run's JSON until this is resolved.",
            ", ".join(r.check.name for r in failed),
        )
    if failed or errored:
        return 1
    if counts[dq.WARN] and args.fail_on_warn:
        logger.error("Warnings present and --fail-on-warn set.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
