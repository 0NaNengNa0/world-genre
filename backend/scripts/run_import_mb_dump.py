"""Import the MusicBrainz artist dump into BigQuery - the batch replacement
for per-artist API lookups.

    python -m scripts.run_import_mb_dump --source artist.tar.xz
    python -m scripts.run_import_mb_dump --source https://.../artist.tar.xz
    python -m scripts.run_import_mb_dump --source artist.tar.xz --limit 5000
    python -m scripts.run_import_mb_dump --source artist.tar.xz --dry-run

WHY THIS EXISTS. `run_extract_artist_meta` resolves one artist per HTTP
request. That is an N+1 pattern - fetch a list, then call once per item - and N
is ~1,600 and growing. MusicBrainz enforces ~1 request/second **per IP**, and
Cloud Run egress shares its addresses, so in production the stage spends ~22
minutes to resolve ~53 artists while absorbing 503s. There is no paid tier that
lifts that limit: MetaBrainz's documented answer for bulk use is the data
dumps, not the API.

So this fetches the whole artist catalogue once and turns origin resolution
into a join. The interview phrasing worth having: *I replaced a rate-limited
per-row API lookup with a bulk dimension load and a join.*

THE DUMP. One newline-delimited JSON record per artist, "in the format
returned by our JSON web service" - which is why the parsing here is the same
`parse_artist_meta` the API path uses. Published twice weekly; core data is
CC0, while supplementary fields (tags, ratings) are CC BY-NC-SA 3.0, so
attribution belongs in the README if genre data from the dump is ever used.
https://musicbrainz.org/doc/Development/JSON_Data_Dumps

NOT A NIGHTLY STAGE. Artist origin is slowly-changing reference data and the
dump is republished twice a week; running this every night would download
gigabytes to learn almost nothing. It belongs on its own weekly or monthly
schedule, which is also why it is absent from scripts/run_pipeline.py.

TWO TABLES, deliberately:

  mb_artists       one row per MBID: country, formed year, type
  mb_artist_names  one row per (normalised name, MBID) - primary AND aliases

The second exists because the charts carry whatever spelling a streaming
service used, and matching on the primary name alone loses every artist known
by another. Flattening aliases into their own table turns matching into an
equality join instead of a fuzzy scan over an array.

Names are normalised **at load time with `cleansing.match_key`**, so both
sides of the eventual join are produced by identical code. That key cannot be
reproduced in SQL - it drops trailing collaborators, which no LOWER(TRIM(...))
will do - so the chart side has to be normalised at load time too rather than
in the query. See the note in `match_key` itself.

TRUNCATE-AND-LOAD, unlike every other table here. `mb_artists` is a mirror of
an external source - nothing else writes to it - so replacing it wholesale is
correct. That is precisely the opposite of `artists`, which `merge_dimension`
protects because enrichment columns live there. Knowing which of the two a
table is determines the write pattern; getting it backwards silently destroys
work.
"""
import argparse
import json
import logging
import tarfile
from datetime import datetime, timezone

from app.services.cleansing import match_key
from app.services.extractors.musicbrainz import parse_aliases, parse_artist_meta

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("run_import_mb_dump")

ARTISTS_TABLE = "mb_artists"
NAMES_TABLE = "mb_artist_names"

# Rows per BigQuery load job. Large enough that the per-job overhead is
# negligible against ~2.5M artists, small enough to stay well inside memory on
# a 1 GiB container.
BATCH_SIZE = 50_000


def artist_row(payload: dict, imported_at: str) -> dict | None:
    """One `mb_artists` row, or None for a record with no usable id.

    Pure: takes a parsed dump record, returns a dict. No file, no client.
    """
    mbid = payload.get("id")
    name = payload.get("name")
    if not mbid or not name:
        return None

    meta = parse_artist_meta(payload)
    return {
        "mbid": mbid,
        "name": name,
        "sort_name": payload.get("sort-name"),
        "country": meta["country"],
        "formed_year": meta["formed_year"],
        # "Person" / "Group" / "Orchestra" ... Kept because it is free here and
        # a plausible future tie-breaker when two artists share a name.
        "artist_type": payload.get("type"),
        "imported_at": imported_at,
    }


def name_rows(payload: dict) -> list[dict]:
    """One row per distinct normalised name this artist answers to.

    `is_primary` marks the artist's own name as opposed to an alias. It is the
    tie-breaker for the ambiguous case: when a normalised name maps to several
    MBIDs, a primary-name match should beat an alias match, and a tie between
    two primaries should resolve to nothing rather than to a guess.
    """
    mbid = payload.get("id")
    if not mbid:
        return []

    primary = payload.get("name")
    rows, seen = [], set()
    for alias in parse_aliases(payload):
        match_name = match_key(alias)
        if not match_name or match_name in seen:
            continue
        seen.add(match_name)
        rows.append(
            {
                "match_name": match_name,
                "mbid": mbid,
                "is_primary": alias == primary,
            }
        )
    return rows


def _iter_lines(stream):
    """Non-empty lines from a streamed tar member, as bytes.

    Chunked by hand rather than wrapped in io.TextIOWrapper: in streaming mode
    tarfile hands back a member object with no seekable(), which TextIOWrapper
    calls on construction. Reading 1 MiB at a time and splitting on newlines
    keeps the whole thing streaming - the archive is gigabytes and must never
    be materialised - and json.loads takes bytes directly.
    """
    buffer = b""
    while True:
        chunk = stream.read(1 << 20)
        if not chunk:
            break
        buffer += chunk
        *complete, buffer = buffer.split(b"\n")
        for line in complete:
            line = line.strip()
            if line:
                yield line
    if buffer.strip():
        yield buffer.strip()


def _is_record_line(line: bytes) -> bool:
    """Does this line look like one of the dump's JSON entity records?"""
    try:
        return isinstance(json.loads(line), dict)
    except json.JSONDecodeError:
        return False


def iter_records(fileobj) -> "object":
    """Yield parsed JSON records from a streamed `.tar.xz` dump.

    Streaming mode (`r|xz`) rather than random access: the archive is
    gigabytes and `r:xz` would want to seek, which a network response body
    cannot do. Members are therefore read in order and only once.

    WHICH MEMBER HOLDS THE DATA IS DECIDED BY CONTENT, NOT BY NAME. An earlier
    version skipped a hardcoded list of metadata filenames - COPYING, README,
    TIMESTAMP, SCHEMA_SEQUENCE - and the real archive promptly produced
    REPLICATION_SEQUENCE, which contains a bare integer. `json.loads` parses
    that happily into an int, and the first `.get("id")` downstream died on
    it. A denylist fails on the first entry you did not think of, and the unit
    test could not have caught it because its fixture contained exactly the
    names the denylist knew about.

    So: peek the first line of each member and read it only if that line
    parses as a JSON object. TIMESTAMP (a date string) fails to parse,
    REPLICATION_SEQUENCE parses as an int, the checksum files fail to parse -
    all skipped without needing to be named.

    Within a data member the rules are stricter: a blank line is skipped, but
    a malformed line or a non-object raises, because silently dropping records
    from a reference import is how a join quietly loses a percent of its rows
    with nothing in the logs.
    """
    with tarfile.open(fileobj=fileobj, mode="r|xz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            stream = tar.extractfile(member)
            if stream is None:
                continue

            lines = _iter_lines(stream)
            first = next(lines, None)
            if first is None or not _is_record_line(first):
                logger.debug("skipping %s (no JSON records)", member.name)
                continue

            logger.info("reading %s", member.name)
            yield json.loads(first)
            for line in lines:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError(
                        f"{member.name}: expected a JSON object per line, got "
                        f"{type(record).__name__}"
                    )
                yield record


def _open_source(source: str):
    """A binary stream over the dump, local path or URL."""
    if source.startswith(("http://", "https://")):
        import requests

        resp = requests.get(source, stream=True, timeout=60)
        resp.raise_for_status()
        resp.raw.decode_content = True
        return resp.raw
    return open(source, "rb")


def _load(table: str, rows: list[dict], truncate: bool) -> None:
    from app.core.bq import _require_bigquery, dataset_id, get_client

    bigquery = _require_bigquery()
    disposition = (
        bigquery.WriteDisposition.WRITE_TRUNCATE
        if truncate
        else bigquery.WriteDisposition.WRITE_APPEND
    )
    get_client().load_table_from_json(
        rows,
        f"{dataset_id()}.{table}",
        job_config=bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=disposition,
            autodetect=False,
            # Run run_init_bq first. Without this, a load job against a missing
            # table creates it - which is exactly what happened here the first
            # time: both mirror tables were born from this load job rather than
            # from schema.sql, so they never carried the keys their DDL
            # declares and the CREATEs have been silent no-ops ever since.
            create_disposition=bigquery.CreateDisposition.CREATE_NEVER,
        ),
    ).result()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source", required=True, help="path or URL of the artist .tar.xz dump"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="stop after N records (smoke test)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="parse and count, write nothing"
    )
    args = parser.parse_args(argv)

    imported_at = datetime.now(timezone.utc).isoformat()
    artists: list[dict] = []
    names: list[dict] = []
    seen = written_artists = written_names = 0
    # The first load of each table truncates; every batch after appends. A
    # failure midway therefore leaves a partial table rather than an empty one,
    # which is the honest trade for not doubling the write cost with a staging
    # table. The import is safe to simply re-run.
    first_artists = first_names = True

    with _open_source(args.source) as raw:
        for payload in iter_records(raw):
            seen += 1
            row = artist_row(payload, imported_at)
            if row:
                artists.append(row)
                names.extend(name_rows(payload))

            if len(artists) >= BATCH_SIZE and not args.dry_run:
                _load(ARTISTS_TABLE, artists, truncate=first_artists)
                _load(NAMES_TABLE, names, truncate=first_names)
                written_artists += len(artists)
                written_names += len(names)
                first_artists = first_names = False
                artists, names = [], []
                logger.info("%d artists, %d names written", written_artists, written_names)

            if args.limit and seen >= args.limit:
                break

    if artists and not args.dry_run:
        _load(ARTISTS_TABLE, artists, truncate=first_artists)
        _load(NAMES_TABLE, names, truncate=first_names)
        written_artists += len(artists)
        written_names += len(names)

    logger.info(
        "Done. %d records read, %d artists, %d names%s.",
        seen,
        written_artists if not args.dry_run else len(artists),
        written_names if not args.dry_run else len(names),
        " (dry run, nothing written)" if args.dry_run else "",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
