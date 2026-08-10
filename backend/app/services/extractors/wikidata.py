"""Look up artist photos from Wikidata / Wikimedia Commons by MusicBrainz ID.

Why this exists alongside the Deezer extractor: Deezer has no photo for a
meaningful slice of well-known artists (Radiohead, Coldplay, The Weeknd and
~50 others in this dataset), and it signals that by returning a normal-looking
URL whose path is the MD5 of the empty string rather than an error. Wikidata
fills those gaps.

Why Wikidata specifically:
  * It joins on an identifier, not a name. Wikidata property P434 IS the
    MusicBrainz artist ID, and Last.fm already hands us an mbid per artist -
    so no fuzzy name matching, which is also how Deezer occasionally returns
    the wrong artist rather than none.
  * It batches. One SPARQL query resolves hundreds of MBIDs at once, unlike
    MusicBrainz's ~1 request/second crawl.
  * Commons images are freely licensed, which matters for a site that will be
    linked publicly - streaming services' artist photos are their assets.

Known limitation: Commons requires a free license, and most commercial promo
photos aren't. Coverage skews Western/older; expect misses for K-pop and
J-pop artists in particular. That's why this is a FALLBACK behind Deezer
rather than a replacement - Deezer covers current chart artists well, and
this covers the canonical acts Deezer forgets.

Pure functions plus one network call, same convention as the other extractors.
"""
import requests

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

# Wikimedia asks that clients identify themselves with contact info and will
# block generic/absent user agents. Replace the URL with your own fork if you
# run this at any volume.
USER_AGENT = "world-genre/1.0 (https://github.com/; portfolio data project)"

# P434 = MusicBrainz artist ID, P18 = image.
_QUERY_TEMPLATE = """SELECT ?mbid ?image WHERE {{
  VALUES ?mbid {{ {values} }}
  ?item wdt:P434 ?mbid ;
        wdt:P18 ?image .
}}"""

# One query per this many MBIDs. Large enough that the whole catalogue is a
# handful of requests, small enough to stay well inside the query service's
# 60-second timeout.
BATCH_SIZE = 250

DEFAULT_THUMBNAIL_WIDTH = 250


def build_sparql(mbids: list[str]) -> str:
    """SPARQL selecting (mbid, image) for the given MusicBrainz artist IDs.

    Only artists that HAVE an image come back - there's no OPTIONAL here, so
    a missing photo simply yields no row rather than a null to filter later.
    """
    values = " ".join(f'"{m}"' for m in mbids)
    return _QUERY_TEMPLATE.format(values=values)


def thumbnail_url(commons_url: str, width: int = DEFAULT_THUMBNAIL_WIDTH) -> str:
    """Resize a Commons Special:FilePath URL.

    P18 returns the ORIGINAL file, which for Commons routinely means a
    multi-megabyte photo - unusable as a card thumbnail. Special:FilePath
    takes a width parameter and serves a scaled version instead. Also upgrades
    the http:// that Wikidata still emits to https, since the page is served
    over https and a mixed-content image is blocked by the browser.
    """
    url = commons_url.replace("http://", "https://", 1)
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}width={width}"


def parse_bindings(payload: dict, width: int = DEFAULT_THUMBNAIL_WIDTH) -> dict[str, str]:
    """SPARQL JSON results -> {mbid: thumbnail_url}.

    Split out from the request so the response shape can be tested without
    network access.
    """
    images: dict[str, str] = {}
    for row in payload.get("results", {}).get("bindings", []):
        mbid = row.get("mbid", {}).get("value")
        image = row.get("image", {}).get("value")
        if not mbid or not image:
            continue
        # An artist can have several P18 values; first one wins rather than
        # last, so results are stable across runs.
        images.setdefault(mbid, thumbnail_url(image, width))
    return images


def fetch_images(
    mbids: list[str],
    width: int = DEFAULT_THUMBNAIL_WIDTH,
    timeout: int = 60,
) -> dict[str, str]:
    """Resolve one batch of MBIDs to thumbnail URLs.

    POST rather than GET: a few hundred MBIDs in a VALUES block overruns what
    the endpoint accepts in a query string.
    """
    if not mbids:
        return {}

    response = requests.post(
        SPARQL_ENDPOINT,
        data={"query": build_sparql(mbids)},
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/sparql-results+json",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_bindings(response.json(), width)
