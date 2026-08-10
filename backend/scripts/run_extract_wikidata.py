"""Fill in artist photos Deezer doesn't have, from Wikidata / Wikimedia Commons.

    data/raw/lastfm/*.json      (artist -> mbid)
    data/raw/deezer/artists.json (who already has a photo)
        -> data/raw/wikidata/artists.json  { "Artist Name": {"mbid", "image"} }

Deliberately a gap-filler, not a full pass: it only asks Wikidata about
artists Deezer had no usable picture for. Deezer covers current chart artists
well; Wikidata covers the canonical acts Deezer is missing (Radiohead,
Coldplay, The Weeknd). Running it over everything would be slower and would
prefer free-licensed but often older photos for artists already covered.

Run from the backend/ directory, after the other extractors:
    python -m scripts.run_extract_lastfm     # source of mbids
    python -m scripts.run_extract_deezer     # primary image source
    python -m scripts.run_extract_wikidata   # fill the gaps
"""
import json
import logging
import time

import requests

from app.core.config import DATA_DIR
from app.services.extractors import deezer, wikidata

LASTFM_DIR = DATA_DIR / "raw" / "lastfm"
DEEZER_ARTISTS_PATH = DATA_DIR / "raw" / "deezer" / "artists.json"
OUTPUT_DIR = DATA_DIR / "raw" / "wikidata"
OUTPUT_PATH = OUTPUT_DIR / "artists.json"

# Wikidata's query service is generous but shared infrastructure; a short
# pause between batches keeps this well-behaved rather than bursty.
PAUSE_BETWEEN_BATCHES = 1.0

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("run_extract_wikidata")


def collect_mbids() -> dict[str, str]:
    """{artist_name: mbid} across all countries, deduped.

    Last.fm returns an empty string rather than omitting the field when it has
    no mbid for an artist, so falsy values are filtered rather than assumed
    absent.
    """
    mbids: dict[str, str] = {}
    for path in sorted(LASTFM_DIR.glob("*.json")):
        payload = json.loads(path.read_text())
        for artist in payload.get("artists", []):
            name, mbid = artist.get("name"), artist.get("mbid")
            if name and mbid and name not in mbids:
                mbids[name] = mbid
    return mbids


def artists_needing_images(all_mbids: dict[str, str]) -> dict[str, str]:
    """Subset of {name: mbid} that Deezer couldn't supply a real photo for.

    Uses deezer.pick_picture rather than a truthiness check, because Deezer
    stores a placeholder URL for artists it has no photo of - those need
    filling too, and look 'present' to a naive check.
    """
    if not DEEZER_ARTISTS_PATH.exists():
        logger.warning("No Deezer data found - treating every artist as needing an image")
        return dict(all_mbids)

    deezer_artists = json.loads(DEEZER_ARTISTS_PATH.read_text())
    return {
        name: mbid
        for name, mbid in all_mbids.items()
        if not deezer.pick_picture(deezer_artists.get(name, {}))
    }


def _load_cache() -> dict[str, dict]:
    if not OUTPUT_PATH.exists():
        return {}
    return json.loads(OUTPUT_PATH.read_text())


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_mbids = collect_mbids()
    needed = artists_needing_images(all_mbids)
    cached = _load_cache()

    # Only ask about artists not already resolved. A previous run's misses
    # aren't cached, so they're retried - a miss can mean "no free-licensed
    # photo exists" but it can equally mean the request failed, and the two
    # aren't distinguishable from the response.
    pending = {name: mbid for name, mbid in needed.items() if name not in cached}

    logger.info(
        "%d artists with mbids, %d lack a Deezer photo, %d already resolved, %d to fetch",
        len(all_mbids),
        len(needed),
        len(needed) - len(pending),
        len(pending),
    )
    if not pending:
        logger.info("Nothing to do.")
        return

    by_mbid = {mbid: name for name, mbid in pending.items()}
    mbid_list = list(by_mbid)
    resolved = dict(cached)
    found = 0

    for start in range(0, len(mbid_list), wikidata.BATCH_SIZE):
        batch = mbid_list[start : start + wikidata.BATCH_SIZE]
        batch_number = start // wikidata.BATCH_SIZE + 1
        try:
            images = wikidata.fetch_images(batch)
        except requests.RequestException as e:
            # One failed batch shouldn't discard the batches that worked -
            # whatever resolved so far still gets written, and the rest are
            # simply retried next run.
            logger.warning("batch %d failed, will retry next run: %s", batch_number, e)
            continue

        for mbid, image in images.items():
            resolved[by_mbid[mbid]] = {"mbid": mbid, "image": image}
        found += len(images)
        logger.info(
            "batch %d: %d/%d artists had a free-licensed photo",
            batch_number,
            len(images),
            len(batch),
        )
        time.sleep(PAUSE_BETWEEN_BATCHES)

    OUTPUT_PATH.write_text(json.dumps(resolved, indent=2))
    logger.info(
        "Done. %d new images (%d total) -> %s", found, len(resolved), OUTPUT_PATH.resolve()
    )


if __name__ == "__main__":
    main()
