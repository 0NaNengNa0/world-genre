"""Tests for resolving the data root to a local path or a bucket.

The whole point of this indirection is that the ~78 filesystem call sites in
app/ and scripts/ never learn about it. So these tests check the two things
that would break that promise: that the local default is untouched when
nothing is configured, and that a gs:// URL produces an object exposing the
same pathlib surface the call sites already use.
"""
from pathlib import Path

import pytest

from app.core import config
from app.core.storage import resolve_data_dir

LOCAL_DEFAULT = Path("/tmp/world-genre-default")


class TestResolveDataDir:
    def test_unset_returns_the_local_default(self):
        # Local dev, the test suite and `npm run offline` all rely on this
        # requiring no configuration whatsoever.
        assert resolve_data_dir(None, LOCAL_DEFAULT) is LOCAL_DEFAULT

    def test_empty_string_is_treated_as_unset(self):
        # An unset env var in a container commonly arrives as "" rather than
        # missing, which would otherwise resolve to a relative path of "".
        assert resolve_data_dir("", LOCAL_DEFAULT) is LOCAL_DEFAULT

    def test_explicit_local_path_overrides_the_default(self):
        assert resolve_data_dir("/mnt/data", LOCAL_DEFAULT) == Path("/mnt/data")

    def test_gs_url_returns_a_cloud_path(self):
        pytest.importorskip("google.cloud.storage")
        from cloudpathlib import GSPath

        resolved = resolve_data_dir("gs://world-genre-raw/data", LOCAL_DEFAULT)
        assert isinstance(resolved, GSPath)

    def test_cloud_path_supports_the_operations_the_codebase_uses(self):
        """Guards the substitution itself rather than any one call site.

        A grep of app/ and scripts/ found exactly these operations against
        data paths and no os.path or shutil anywhere, which is what makes a
        drop-in replacement possible. If that set ever grows beyond what
        CloudPath implements, this fails here instead of at runtime in a
        Cloud Run job.
        """
        pytest.importorskip("google.cloud.storage")
        resolved = resolve_data_dir("gs://world-genre-raw/data", LOCAL_DEFAULT)
        for operation in (
            "read_text",
            "write_text",
            "exists",
            "mkdir",
            "glob",
            "open",
            "stat",
        ):
            assert hasattr(resolved, operation), operation

    def test_joining_produces_the_expected_object_key(self):
        pytest.importorskip("google.cloud.storage")
        resolved = resolve_data_dir("gs://world-genre-raw/data", LOCAL_DEFAULT)
        assert str(resolved / "raw" / "kworb" / "us.json") == (
            "gs://world-genre-raw/data/raw/kworb/us.json"
        )


class TestCodePathsStayLocal:
    """Seeds and SQL ship in the image and must never follow DATA_DIR.

    The query files were previously reached through `DATA_DIR.parent`, so
    pointing DATA_DIR at a bucket would have sent the API looking for its own
    .sql files in GCS - at import time, before any request.
    """

    def test_sql_and_seed_dirs_are_local_paths(self):
        assert isinstance(config.SQL_DIR, Path)
        assert isinstance(config.SEEDS_DIR, Path)

    def test_sql_dir_actually_contains_the_queries(self):
        assert (config.SQL_DIR / "bigquery" / "schema.sql").exists()
        assert (config.SQL_DIR / "bigquery" / "queries" / "hidden_gems.sql").exists()

    def test_sql_dir_is_not_derived_from_the_data_dir(self):
        # The specific regression: SQL_DIR must be rooted at BACKEND_ROOT, so
        # that a gs:// DATA_DIR leaves it entirely alone.
        assert config.SQL_DIR == config.BACKEND_ROOT / "sql"
