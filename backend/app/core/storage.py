"""Where the pipeline's data lives - a local directory or a cloud bucket.

Every extractor writes JSON under `DATA_DIR` and every later stage reads it
back, across 78 call sites. On a container platform that stops working for a
reason that isn't a crash: the filesystem is ephemeral, so the caches survive
only until the container exits. The MusicBrainz cache is the one that hurts -
without it, every run re-crawls ~1,050 artists at 1.5s pacing, turning a
warm 8-minute pipeline back into a 33-minute one, every single time.

The fix is to let `DATA_DIR` point at a bucket. `cloudpathlib.GSPath`
implements the same interface as `pathlib.Path` - `/`, `read_text`,
`write_text`, `exists`, `mkdir`, `glob`, `open` - which is the entire
vocabulary this codebase uses (verified: no `os.path`, no `shutil`
anywhere). So the call sites need no changes at all; only the root does.

Seeds and SQL deliberately do NOT go through here. They're source files that
ship inside the image, not data the pipeline produces - see SQL_DIR in
config.py.
"""
import os
from pathlib import Path

GCS_SCHEME = "gs://"


def resolve_data_dir(raw: str | None, local_default: Path):
    """DATA_DIR as a Path (local) or GSPath (bucket), from an env var.

    Returns `local_default` when unset, so local development, the test suite
    and `npm run offline` keep working with no configuration and no cloud
    dependency installed.

    cloudpathlib is imported lazily rather than at module scope on purpose:
    it's only needed when a bucket is actually configured, so local runs and
    CI don't have to install it, and a missing install surfaces here with a
    clear cause instead of at import time in an unrelated module.
    """
    if not raw:
        return local_default

    if raw.startswith(GCS_SCHEME):
        # Two distinct failures, both meaning "the gs extra isn't installed":
        # cloudpathlib missing entirely is an ImportError, while cloudpathlib
        # present without google-cloud-storage raises its own
        # MissingDependenciesError at construction, not at import. Catching
        # only ImportError misses the second and far more likely case -
        # `pip install cloudpathlib` succeeds and looks correct.
        try:
            from cloudpathlib import GSPath

            return GSPath(raw)
        except Exception as e:
            if type(e).__name__ in {"ImportError", "MissingDependenciesError"}:
                raise RuntimeError(
                    f"DATA_DIR is {raw!r} but the GCS support isn't installed. "
                    "Install the extra: pip install 'cloudpathlib[gs]'"
                ) from e
            raise

    # An explicit local override, which is what the Docker image uses to
    # point at a mounted volume.
    return Path(raw)


def data_dir_from_env(local_default: Path):
    return resolve_data_dir(os.environ.get("DATA_DIR"), local_default)
