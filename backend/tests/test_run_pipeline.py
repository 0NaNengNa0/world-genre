"""The pipeline driver's sequencing and exit codes.

This script is the Cloud Run Job's entrypoint, so its behaviour under failure
is the difference between "yesterday's data keeps serving" and "the site shows
a half-built day". None of that needs BigQuery to test: the stage list is
data, the selection is a pure function, and the failure handling only needs
stages that raise or return on cue.

The stage order itself was recovered from the nightly job's Cloud Logging
output in September 2026, after it turned out the driver running production
existed only inside a container image. The order test below is what stops it
drifting away from what has actually been running.
"""
import pytest

from scripts import run_pipeline
from scripts.run_pipeline import STAGES, Stage, select_stages


class TestStageList:
    def test_order_matches_the_recovered_production_sequence(self):
        assert [s.name for s in STAGES] == [
            "init_bq",
            "extract_kworb",
            "extract_lastfm",
            "extract_musicbrainz",
            "extract_deezer",
            "extract_wikidata",
            "cleanse",
            "load",
            "resolve_artists",
            "enrich_artists",
            "enrich_genres",
            "validate",
            "publish",
        ]

    def test_every_stage_points_at_a_real_module(self):
        import importlib.util

        missing = [s.module for s in STAGES if importlib.util.find_spec(s.module) is None]
        assert not missing, f"no such module: {missing}"

    def test_only_the_gate_is_marked_as_a_gate(self):
        # The flag changes how the return value is interpreted, so a second
        # stage acquiring it by accident would silently turn a return value
        # into a blocking verdict.
        assert [s.name for s in STAGES if s.gate] == ["validate"]

    def test_the_gate_runs_immediately_before_publish(self):
        names = [s.name for s in STAGES]
        assert names.index("validate") == names.index("publish") - 1

    def test_enrichment_runs_after_load(self):
        # Both use the rows load just wrote as their worklist.
        names = [s.name for s in STAGES]
        assert names.index("load") < names.index("enrich_artists")
        assert names.index("load") < names.index("enrich_genres")

    def test_wikidata_runs_after_deezer(self):
        # It only asks about the artists deezer failed to find a photo for.
        names = [s.name for s in STAGES]
        assert names.index("extract_deezer") < names.index("extract_wikidata")


class TestSelectStages:
    def test_default_is_everything(self):
        assert select_stages(STAGES) == STAGES

    def test_from_resumes_at_that_stage(self):
        selected = select_stages(STAGES, start="load")
        assert selected[0].name == "load"
        assert selected[-1].name == "publish"

    def test_only_returns_exactly_one(self):
        assert [s.name for s in select_stages(STAGES, only="publish")] == ["publish"]

    def test_only_wins_over_from(self):
        selected = select_stages(STAGES, start="load", only="cleanse")
        assert [s.name for s in selected] == ["cleanse"]

    def test_unknown_stage_raises_rather_than_running_everything(self):
        # A typo that silently ran the full 36-minute pipeline would be a
        # nasty way to learn you misspelled a stage name.
        with pytest.raises(ValueError, match="unknown stage"):
            select_stages(STAGES, start="lodad")
        with pytest.raises(ValueError, match="unknown stage"):
            select_stages(STAGES, only="publsh")


class FakeStages:
    """Swap the real stage list for callables that record and misbehave on cue."""

    def __init__(self, monkeypatch, behaviours):
        self.ran = []
        self.behaviours = behaviours
        stages = [
            Stage(name, f"fake.{name}", gate=(name == "validate"))
            for name in behaviours
        ]
        monkeypatch.setattr(run_pipeline, "STAGES", stages)
        monkeypatch.setattr(run_pipeline, "run_stage", self._run)

    def _run(self, stage):
        self.ran.append(stage.name)
        outcome = self.behaviours[stage.name]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TestRunOrder:
    def test_happy_path_runs_everything_and_exits_zero(self, monkeypatch):
        fake = FakeStages(monkeypatch, {"load": None, "validate": 0, "publish": None})
        assert run_pipeline.main([]) == 0
        assert fake.ran == ["load", "validate", "publish"]

    def test_a_failing_stage_stops_the_run(self, monkeypatch):
        # Every later stage reads what an earlier one wrote, so continuing
        # would publish a half-built day rather than yesterday's complete one.
        fake = FakeStages(
            monkeypatch,
            {"load": RuntimeError("bigquery exploded"), "validate": 0, "publish": None},
        )
        assert run_pipeline.main([]) == 1
        assert fake.ran == ["load"]

    def test_a_blocked_gate_skips_publish_only(self, monkeypatch):
        fake = FakeStages(monkeypatch, {"load": None, "validate": 1, "publish": None})
        assert run_pipeline.main([]) == 1
        # load kept its work; publish never ran, so the serving bucket still
        # holds the previous run's payloads.
        assert fake.ran == ["load", "validate"]

    def test_a_gate_that_could_not_run_exits_three(self, monkeypatch):
        # Translated, not preserved: run_validate's own contract is 0/1/2, but
        # 2 is argparse's usage-error code, so the driver reports 3. The job's
        # retry policy can then tell a transient check failure from a bad
        # command line - see TestExitCodesDoNotCollideWithArgparse.
        FakeStages(monkeypatch, {"load": None, "validate": 2, "publish": None})
        assert run_pipeline.main([]) == 3

    def test_only_publish_skips_the_gate_entirely(self, monkeypatch):
        # Deliberate: --only is an operator escape hatch for re-running one
        # stage by hand, and it does what it says.
        fake = FakeStages(monkeypatch, {"load": None, "validate": 1, "publish": None})
        assert run_pipeline.main(["--only", "publish"]) == 0
        assert fake.ran == ["publish"]


class TestCompletionLineIsAMonitoringContract:
    """The success line is a production signal, not just a log message.

    infra/monitoring/pipeline_completed.yaml builds a log-based metric by
    matching the text "pipeline done in", and the staleness alert fires when
    that metric stops appearing for 30 hours. So renaming the line does not
    break a log - it silently disables the only thing watching for a pipeline
    that never ran, and disables it in the direction that looks healthy: no
    metric, no data points, and an absence alert that has nothing to compare
    against.

    Matching on log text is a coupling worth being honest about. This test is
    the compensating control: the string lives in two repos-worth of places,
    so pin it here and a rename fails CI instead of failing silently in
    production six weeks later.
    """

    SIGNAL = "pipeline done in"

    def test_the_signal_string_is_emitted_on_success(self, monkeypatch, caplog):
        FakeStages(monkeypatch, {"load": None, "validate": 0, "publish": None})
        with caplog.at_level("INFO", logger="run_pipeline"):
            assert run_pipeline.main([]) == 0
        assert any(self.SIGNAL in r.getMessage() for r in caplog.records), (
            "the completion line changed - update "
            "infra/monitoring/pipeline_completed.yaml in the same commit"
        )

    def test_it_is_not_emitted_when_the_gate_blocks(self, monkeypatch, caplog):
        # The metric must mean "a complete, publishable run finished", not
        # "the container exited". A blocked gate is a run whose output nobody
        # should treat as fresh, so it must not tick the freshness counter.
        FakeStages(monkeypatch, {"load": None, "validate": 1, "publish": None})
        with caplog.at_level("INFO", logger="run_pipeline"):
            assert run_pipeline.main([]) == 1
        assert not any(self.SIGNAL in r.getMessage() for r in caplog.records)

    def test_it_is_not_emitted_when_a_stage_raises(self, monkeypatch, caplog):
        FakeStages(monkeypatch, {"load": RuntimeError("boom"), "validate": 0})
        with caplog.at_level("INFO", logger="run_pipeline"):
            assert run_pipeline.main([]) == 1
        assert not any(self.SIGNAL in r.getMessage() for r in caplog.records)


class TestStageFlagsMatchTheRealSignatures:
    """`takes_argv` must agree with each stage module's actual main().

    This is the structural fix for a bug that reached production on
    2026-09-16. run_resolve_artists was added with main(argv=None) - an argv
    signature, for --dry-run - but without the flag that says so, because the
    flag was called `gate` and resolve is not a gate. run_pipeline therefore
    called it as main(), argparse fell back to sys.argv, and the stage
    rejected the DRIVER's `--from load` as an unrecognised argument.

    Reading the declaration against the reality is the only check that scales:
    any future stage whose main() grows or loses a parameter fails here, in
    CI, in a second - instead of at 03:00 in a Cloud Run job, and only on the
    runs that happen to pass an argument at all.
    """

    @pytest.mark.parametrize("stage", STAGES, ids=lambda s: s.name)
    def test_declared_flag_matches_the_function(self, stage):
        import importlib
        import inspect

        main = importlib.import_module(stage.module).main
        accepts_argv = bool(inspect.signature(main).parameters)
        assert stage.takes_argv == accepts_argv, (
            f"{stage.name}: main() "
            f"{'takes' if accepts_argv else 'takes no'} arguments but "
            f"takes_argv={stage.takes_argv}. Given None, argparse reads "
            f"sys.argv and parses run_pipeline's own flags."
        )

    def test_the_gate_also_takes_argv(self):
        # Not implied - asserted. The gate is called with [] for the same
        # reason, and losing that would reintroduce the original bug in the
        # one stage whose failure is hardest to read.
        gate = [s for s in STAGES if s.gate]
        assert gate and all(s.takes_argv for s in gate)


class TestExitCodesDoNotCollideWithArgparse:
    """2 belongs to argparse. Ours is 3.

    A driver that used 2 for "the gate could not run" gives an operator no
    way to tell a transient check failure - worth one retry - from a
    malformed command line, which is worth none. The job runs with
    maxRetries=1, so that ambiguity is a wasted 30-minute run.
    """

    def test_check_error_is_not_two(self):
        assert run_pipeline.EXIT_CHECK_ERROR == 3

    def test_a_gate_that_could_not_run_is_translated(self, monkeypatch):
        # run_validate keeps its own 0/1/2 contract; the driver maps it.
        FakeStages(monkeypatch, {"load": None, "validate": 2, "publish": None})
        assert run_pipeline.main([]) == 3

    def test_a_gate_that_failed_on_data_is_still_one(self, monkeypatch):
        FakeStages(monkeypatch, {"load": None, "validate": 1, "publish": None})
        assert run_pipeline.main([]) == 1
