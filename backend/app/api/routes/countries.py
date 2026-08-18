"""Country and artist endpoints, served from published JSON.

Every response here was assembled by scripts/run_publish.py at pipeline time.
The routes do lookups, not queries - see app/services/published.py for why the
warehouse is deliberately not in the request path.
"""
from fastapi import APIRouter, HTTPException

from app.schemas.countries import (
    CountriesResponse,
    CountryDetail,
    GenreDetail,
    GlobalArtistsResponse,
)
from app.services import published

router = APIRouter(tags=["countries"])

# Distinguishes "nothing published yet" from "no such country". The first is a
# deployment state that resolves itself on the next pipeline run, the second
# is a client error - returning 404 for both would send someone hunting for a
# missing country when the real answer is that run_publish hasn't run.
_NOT_PUBLISHED = "No published data yet. Run the pipeline's publish step."


@router.get("/countries", response_model=CountriesResponse)
def list_countries() -> CountriesResponse:
    summaries = published.get_country_summaries()
    if summaries is None:
        raise HTTPException(status_code=503, detail=_NOT_PUBLISHED)
    return CountriesResponse(countries=summaries)


@router.get("/artists/global", response_model=GlobalArtistsResponse)
def global_artists() -> GlobalArtistsResponse:
    artists = published.get_global_artists()
    if artists is None:
        raise HTTPException(status_code=503, detail=_NOT_PUBLISHED)
    return GlobalArtistsResponse(artists=artists)


@router.get("/countries/{code}", response_model=CountryDetail)
def country_detail(code: str) -> CountryDetail:
    detail = published.get_country_detail(code.lower())
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Unknown country: {code}")
    return CountryDetail(**detail)


@router.get("/countries/{code}/genres/{genre}", response_model=GenreDetail)
def genre_detail(code: str, genre: str) -> GenreDetail:
    detail = published.get_genre_detail(code.lower(), genre)
    if detail is None:
        raise HTTPException(
            status_code=404, detail=f"No data for {genre} in {code}"
        )
    return GenreDetail(**detail)
